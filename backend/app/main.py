import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .db import get_conn, init_db

DOW_COLS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

app = FastAPI(title="Adelaide Transit Predictor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def root():
    return {"status": "ok", "service": "Adelaide Transit Predictor"}


@app.get("/stops")
def list_stops(
    lat: float = Query(None),
    lon: float = Query(None),
    radius_km: float = 1.0,
    q: str | None = None,
):
    with get_conn() as conn:
        if lat is not None and lon is not None:
            # rough bounding-box filter (1 deg lat ~ 111 km)
            dlat = radius_km / 111.0
            dlon = radius_km / (111.0 * max(0.1, abs(lat) / 90.0 + 0.5))
            rows = conn.execute("""
                SELECT * FROM stops
                WHERE stop_lat BETWEEN ? AND ?
                  AND stop_lon BETWEEN ? AND ?
                LIMIT 500
            """, (lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchall()
        elif q:
            rows = conn.execute(
                "SELECT * FROM stops WHERE stop_name LIKE ? LIMIT 100",
                (f"%{q}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM stops LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def _typical_delay(conn, route_id, stop_id, hour, is_weekend):
    """
    Return (p25, p50, p75, samples) for this route+stop, narrowing the time
    window only as needed:
      1. exact hour, same day-type
      2. +/- 1 hour, same day-type
      3. any hour, same day-type
      4. any hour, any day-type
    """
    day_filter = "1 if is_weekend else 0"
    base = """
        SELECT delay_seconds FROM realtime_updates
        WHERE route_id = ? AND stop_id = ? AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
    """

    queries = [
        ("exact",
         base + """
           AND CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) = ?
           AND (CAST(strftime('%w', datetime(fetched_at,'unixepoch','localtime')) AS INT) IN (0,6)) = ?
         """,
         (route_id, stop_id, hour, 1 if is_weekend else 0)),
        ("±1h",
         base + """
           AND ABS(CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) - ?) <= 1
           AND (CAST(strftime('%w', datetime(fetched_at,'unixepoch','localtime')) AS INT) IN (0,6)) = ?
         """,
         (route_id, stop_id, hour, 1 if is_weekend else 0)),
        ("all-hours-day-type",
         base + """
           AND (CAST(strftime('%w', datetime(fetched_at,'unixepoch','localtime')) AS INT) IN (0,6)) = ?
         """,
         (route_id, stop_id, 1 if is_weekend else 0)),
        ("all", base, (route_id, stop_id)),
    ]

    for scope, sql, params in queries:
        rows = conn.execute(sql + " ORDER BY delay_seconds", params).fetchall()
        if len(rows) >= 3:
            d = [r["delay_seconds"] for r in rows]
            n = len(d)
            return {
                "p25_sec": d[n // 4],
                "p50_sec": d[n // 2],
                "p75_sec": d[(3 * n) // 4],
                "samples": n,
                "scope": scope,
                "day_type": "weekend" if is_weekend else "weekday",
            }
    return None


@app.get("/stop/{stop_id}/next")
def next_arrivals(stop_id: str, limit: int = 5):
    """Latest realtime predictions at this stop + typical-delay annotations."""
    now = int(time.time())
    with get_conn() as conn:
        stop = conn.execute(
            "SELECT * FROM stops WHERE stop_id = ?", (stop_id,)
        ).fetchone()
        if not stop:
            raise HTTPException(404, "stop not found")

        rows = conn.execute("""
            SELECT u.trip_id, u.route_id, u.predicted_arrival, u.delay_seconds, u.has_gps,
                   r.route_short_name, r.route_long_name, t.trip_headsign
            FROM realtime_updates u
            LEFT JOIN routes r ON r.route_id = u.route_id
            LEFT JOIN trips  t ON t.trip_id  = u.trip_id
            WHERE u.stop_id = ?
              AND u.predicted_arrival >= ?
              AND u.id IN (
                SELECT MAX(id) FROM realtime_updates
                WHERE stop_id = ? GROUP BY trip_id
              )
            ORDER BY u.predicted_arrival ASC
            LIMIT ?
        """, (stop_id, now, stop_id, limit)).fetchall()

        arrivals = []
        for r in rows:
            a = dict(r)
            arr_dt = datetime.fromtimestamp(a["predicted_arrival"])
            is_weekend = arr_dt.weekday() >= 5
            a["typical"] = _typical_delay(
                conn, a["route_id"], stop_id, arr_dt.hour, is_weekend
            )
            arrivals.append(a)

    return {"stop": dict(stop), "arrivals": arrivals, "server_time": now}


@app.get("/route/{route_id}/reliability")
def route_reliability(route_id: str):
    """Average delay by hour-of-day for this route."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
              CAST(strftime('%H', datetime(fetched_at, 'unixepoch', 'localtime')) AS INTEGER) AS hour,
              ROUND(AVG(delay_seconds), 1) AS avg_delay,
              COUNT(*) AS samples
            FROM realtime_updates
            WHERE route_id = ? AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
            GROUP BY hour
            ORDER BY hour
        """, (route_id,)).fetchall()
    return {"route_id": route_id, "by_hour": [dict(r) for r in rows]}


@app.get("/stop/{stop_id}/reliability")
def stop_reliability(stop_id: str):
    """Reliability for this stop: combined per-hour + per-route breakdown."""
    with get_conn() as conn:
        by_hour = conn.execute("""
            SELECT
              CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) AS hour,
              ROUND(AVG(delay_seconds), 1) AS avg_delay,
              COUNT(*) AS samples
            FROM realtime_updates
            WHERE stop_id = ? AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
            GROUP BY hour
            ORDER BY hour
        """, (stop_id,)).fetchall()

        by_route = conn.execute("""
            SELECT
              u.route_id,
              r.route_short_name,
              ROUND(AVG(u.delay_seconds), 1) AS avg_delay,
              ROUND(MIN(u.delay_seconds), 0) AS min_delay,
              ROUND(MAX(u.delay_seconds), 0) AS max_delay,
              COUNT(*) AS samples
            FROM realtime_updates u
            LEFT JOIN routes r ON r.route_id = u.route_id
            WHERE u.stop_id = ? AND u.delay_seconds IS NOT NULL AND ABS(u.delay_seconds) <= 7200 AND u.has_gps = 1
            GROUP BY u.route_id
            HAVING samples >= 5
            ORDER BY ABS(avg_delay) DESC
        """, (stop_id,)).fetchall()

        # Per-route per-time-bucket breakdown
        bucket_rows = conn.execute("""
            SELECT
              route_id,
              CASE
                WHEN h BETWEEN 6 AND 8   THEN 'morning'
                WHEN h BETWEEN 9 AND 14  THEN 'midday'
                WHEN h BETWEEN 15 AND 18 THEN 'evening'
                ELSE 'night'
              END AS bucket,
              ROUND(AVG(delay_seconds), 1) AS avg_delay,
              COUNT(*) AS samples
            FROM (
              SELECT route_id, delay_seconds,
                CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) AS h
              FROM realtime_updates
              WHERE stop_id = ? AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
            )
            GROUP BY route_id, bucket
            HAVING samples >= 3
        """, (stop_id,)).fetchall()

        buckets_by_route = {}
        for r in bucket_rows:
            buckets_by_route.setdefault(r["route_id"], {})[r["bucket"]] = {
                "avg_delay": r["avg_delay"], "samples": r["samples"],
            }

    by_route_out = []
    for r in by_route:
        d = dict(r)
        d["buckets"] = buckets_by_route.get(r["route_id"], {})
        by_route_out.append(d)

    return {
        "stop_id": stop_id,
        "by_hour": [dict(r) for r in by_hour],
        "by_route": by_route_out,
    }


def _parse_hms(t: str) -> int:
    """GTFS times can exceed 24:00:00. Return seconds-since-midnight."""
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


@app.get("/trip/plan")
def plan_trip(
    from_stop: str = Query(..., alias="from"),
    to_stop: str = Query(..., alias="to"),
    arrive_by: str | None = Query(None, description="HH:MM 24h, today"),
    limit: int = 5,
):
    """
    Direct trips (no transfers) from `from` to `to`, ordered latest-departure first.
    Each option includes scheduled times and the typical delay observed for that
    route at the destination's arrival hour.
    """
    now = datetime.now()
    today_dow = DOW_COLS[(now.weekday() + 1) % 7]  # weekday: Mon=0 -> sunday-indexed
    today_str = now.strftime("%Y%m%d")
    arrive_secs = _parse_hms(arrive_by + ":00") if arrive_by else 30 * 3600

    with get_conn() as conn:
        has_calendar = conn.execute("SELECT COUNT(*) AS n FROM calendar").fetchone()["n"] > 0
        cal_filter = ""
        params = [from_stop, to_stop]
        if has_calendar:
            cal_filter = f"""
                AND t.service_id IN (
                  SELECT service_id FROM calendar
                  WHERE {today_dow} = 1
                    AND start_date <= ? AND end_date >= ?
                )
            """
            params += [today_str, today_str]

        rows = conn.execute(f"""
            SELECT
              t.trip_id, t.route_id, t.trip_headsign,
              r.route_short_name,
              sf.departure_time AS depart_at,
              st.arrival_time   AS arrive_at,
              sf.stop_sequence  AS seq_from,
              st.stop_sequence  AS seq_to
            FROM stop_times sf
            JOIN stop_times st ON st.trip_id = sf.trip_id
                              AND st.stop_sequence > sf.stop_sequence
            JOIN trips  t ON t.trip_id  = sf.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE sf.stop_id = ? AND st.stop_id = ?
            {cal_filter}
            ORDER BY sf.departure_time
        """, params).fetchall()

    options = []
    for row in rows:
        arr_secs = _parse_hms(row["arrive_at"])
        if arr_secs > arrive_secs:
            continue
        options.append(dict(row))

    options = options[-limit:][::-1]  # latest first

    # Annotate each option with typical delay at arrival hour for that route
    with get_conn() as conn:
        for o in options:
            hr = _parse_hms(o["arrive_at"]) // 3600 % 24
            d = conn.execute("""
                SELECT ROUND(AVG(delay_seconds),0) AS avg_delay, COUNT(*) AS samples
                FROM realtime_updates
                WHERE route_id = ?
                  AND CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) = ?
                  AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
            """, (o["route_id"], hr)).fetchone()
            o["typical_delay_sec"] = int(d["avg_delay"]) if d["avg_delay"] is not None else None
            o["delay_samples"] = d["samples"]

    return {
        "from": from_stop, "to": to_stop, "arrive_by": arrive_by,
        "service_day_filter": has_calendar,
        "options": options,
    }


@app.get("/routes")
def list_routes():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM routes ORDER BY route_short_name"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/stats")
def stats():
    with get_conn() as conn:
        counts = {}
        for tbl in ("routes", "stops", "trips", "stop_times", "realtime_updates"):
            counts[tbl] = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()["n"]
    return counts
