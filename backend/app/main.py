import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from datetime import time as dtime
import anyio.to_thread
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .db import get_conn, init_db

DOW_COLS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
LOOKBACK_SECONDS = 30 * 60      # keep buses whose scheduled time just passed, they may still be coming
LATEST_GTFS_SECONDS = 30 * 3600  # no trip in the feed runs later than 30:00:00
HHMM = re.compile(r"^([01]?\d|2[0-9]):[0-5]\d$")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    # Few worker threads: extra requests queue instead of all fighting for the
    # CPU of a tiny server, which previously starved sshd and froze the box.
    anyio.to_thread.current_default_thread_limiter().total_tokens = 6
    yield


app = FastAPI(title="Adelaide Transit Predictor", lifespan=lifespan)

# A public, read-only API with no cookies, so any origin may read it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _service_midnight(day: date) -> int:
    """GTFS times count from 'noon minus 12 hours', which stays right on daylight-saving days."""
    return int(datetime.combine(day, dtime(12)).timestamp()) - 12 * 3600


def _hms(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _services_on(conn, day: date, table: str) -> bool:
    """Fill a per-connection temp table with the service_ids running on this service day.
    Returns False when no calendar is loaded, which means 'do not filter'.
    Uses calendar.txt plus calendar_dates.txt, so public holidays get their own timetable.
    A temp table works on every SQLite build (no JSON1 needed) and never touches the main database."""
    has_any = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM calendar) OR EXISTS(SELECT 1 FROM calendar_dates) AS n"
    ).fetchone()["n"]
    if not has_any:
        return False
    ymd = day.strftime("%Y%m%d")
    dow = DOW_COLS[(day.weekday() + 1) % 7]
    conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {table} (service_id TEXT PRIMARY KEY)")
    conn.execute(f"DELETE FROM {table}")
    conn.execute(f"""
        INSERT OR IGNORE INTO {table}
        SELECT service_id FROM calendar
        WHERE {dow} = 1 AND start_date <= ? AND end_date >= ?
          AND service_id NOT IN (SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 2)
        UNION
        SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 1
    """, (ymd, ymd, ymd, ymd))
    return True


_cache = {}


def _cached(key, ttl, fn):
    """Tiny in-process TTL cache. History-based stats change slowly, so
    recomputing them on every poll only burns CPU."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    if len(_cache) > 20000:  # crude bound on memory: start over rather than grow forever
        _cache.clear()
    _cache[key] = (now, val)
    return val


@app.get("/")
def root():
    return {"status": "ok", "service": "Adelaide Transit Predictor"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stops")
def list_stops(
    lat: float = Query(None, ge=-90, le=90),
    lon: float = Query(None, ge=-180, le=180),
    radius_km: float = Query(1.0, gt=0, le=5),
    q: str | None = Query(None, min_length=2, max_length=60),
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
            like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            # An exact stop ID (the number on the stop sign) matches too, and comes first.
            rows = conn.execute(
                "SELECT * FROM stops WHERE stop_id = ? OR stop_name LIKE ? ESCAPE '\\' "
                "ORDER BY stop_id = ? DESC LIMIT 100",
                (q, f"%{like}%", q),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM stops LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def _typical_delay(conn, route_id, stop_id, hour, is_weekend):
    """
    Return p10/p25/p50/p75 delay and sample count for this route+stop, narrowing the time
    window only as needed:
      1. exact hour, same day-type
      2. +/- 1 hour, same day-type
      3. any hour, same day-type
      4. any hour, any day-type
    """
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
           AND ((CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) - ? + 24) % 24) IN (0, 1, 23)
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
                "p10_sec": d[n // 10],
                "p25_sec": d[n // 4],
                "p50_sec": d[n // 2],
                "p75_sec": d[(3 * n) // 4],
                "samples": n,
                "scope": scope,
                "day_type": "weekend" if is_weekend else "weekday",
            }

    # realtime_updates only keeps a rolling window (cleanup.py aggregates and
    # deletes older rows). Fall back to that older history rather than
    # reporting "no data" once a route+stop ages out of the raw table.
    # The rolled-up table only keeps hourly mean/min/max, not percentiles. Report the
    # sample-weighted mean for every band rather than passing min/max off as quartiles.
    agg = conn.execute("""
        SELECT SUM(avg_delay * samples) / SUM(samples) AS mean, SUM(samples) AS samples
        FROM delay_hourly
        WHERE route_id = ? AND stop_id = ?
    """, (route_id, stop_id)).fetchone()
    if agg and agg["samples"] and agg["samples"] >= 3:
        mean = int(round(agg["mean"]))
        return {
            "p10_sec": mean, "p25_sec": mean, "p50_sec": mean, "p75_sec": mean,
            "samples": agg["samples"],
            "scope": "all-time-aggregated",
            "day_type": "weekend" if is_weekend else "weekday",
        }
    return None


@app.get("/stop/{stop_id}/next")
def next_arrivals(stop_id: str, limit: int = Query(50, ge=1, le=100)):
    """
    All remaining scheduled arrivals at this stop (until last service),
    merged with live realtime data where the bus is currently tracked, plus
    typical-delay annotations from history.
    """
    now = int(time.time())
    today = datetime.now().date()
    # The collector files every update under the date it was fetched, so live rows are always today's.
    today_sd = today.strftime("%Y-%m-%d")

    with get_conn() as conn:
        stop = conn.execute(
            "SELECT * FROM stops WHERE stop_id = ?", (stop_id,)
        ).fetchone()
        if not stop:
            raise HTTPException(404, "stop not found")

        candidates = []  # (row, service midnight, is_weekend)
        last_service = None
        # Yesterday's service day still runs after midnight (GTFS times like 25:10:00).
        for day in (today - timedelta(days=1), today):
            midnight_ts = _service_midnight(day)
            from_secs = max(0, now - LOOKBACK_SECONDS - midnight_ts)
            if from_secs >= LATEST_GTFS_SECONDS:
                continue
            filtered = _services_on(conn, day, "svc_day")
            cal_filter = "AND t.service_id IN (SELECT service_id FROM svc_day)" if filtered else ""
            params = [today_sd, stop_id, _hms(from_secs), limit + 12]
            rows = conn.execute(f"""
                SELECT st.trip_id, st.arrival_time, st.stop_sequence,
                       t.route_id, t.trip_headsign,
                       r.route_short_name, r.route_long_name, r.route_type,
                       u.predicted_arrival, u.delay_seconds, u.has_gps
                FROM stop_times st
                JOIN trips  t ON t.trip_id  = st.trip_id
                JOIN routes r ON r.route_id = t.route_id
                LEFT JOIN realtime_updates u
                       ON u.trip_id = st.trip_id AND u.stop_id = st.stop_id
                      AND u.service_date = ?
                WHERE st.stop_id = ?
                  {cal_filter}
                  AND st.arrival_time >= ?
                ORDER BY st.arrival_time
                LIMIT ?
            """, params).fetchall()
            candidates += [(r, midnight_ts, day.weekday() >= 5) for r in rows]
            # The real last arrival of the service day, not just the last of the rows fetched above.
            last = conn.execute(f"""
                SELECT MAX(st.arrival_time) AS t
                FROM stop_times st
                JOIN trips t ON t.trip_id = st.trip_id
                WHERE st.stop_id = ?
                  {cal_filter}
            """, (stop_id,)).fetchone()["t"]
            if last:
                last_service = max(last_service or 0, midnight_ts + _parse_hms(last))

        # A stored prediction more than 3h from this scheduled instance is not
        # trustworthy (a stale row from an earlier run of the same trip_id, or a
        # service-day boundary miscalculation). Reject it and fall back to schedule.
        MAX_PRED_DRIFT = 3 * 3600
        arrivals = []
        for r, midnight_ts, is_weekend in candidates:
            sched_epoch = midnight_ts + _parse_hms(r["arrival_time"])
            pred_raw = r["predicted_arrival"]
            delay = r["delay_seconds"]
            if pred_raw and abs(pred_raw - sched_epoch) <= MAX_PRED_DRIFT:
                predicted = pred_raw
            else:
                predicted = sched_epoch
                delay = None
            # Skip anything already gone (more than 2 min past its predicted time).
            if predicted < now - 120:
                continue
            hour = datetime.fromtimestamp(sched_epoch).hour
            typical = _cached(
                ("typ", r["route_id"], stop_id, hour, is_weekend), 1800,
                lambda: _typical_delay(conn, r["route_id"], stop_id, hour, is_weekend),
            )
            arrivals.append({
                "trip_id": r["trip_id"],
                "route_id": r["route_id"],
                "route_short_name": r["route_short_name"],
                "route_long_name": r["route_long_name"],
                "route_type": r["route_type"],
                "trip_headsign": r["trip_headsign"],
                "scheduled_arrival": sched_epoch,
                "predicted_arrival": predicted,
                "delay_seconds": delay,
                "has_gps": r["has_gps"] or 0,
                "typical": typical,
            })

    # Two services can run the same journey on the same day (two trip_ids with the same
    # route, headsign and time). Show it once, keeping the copy that has live data.
    best = {}
    for a in arrivals:
        k = (a["route_short_name"], a["trip_headsign"], a["scheduled_arrival"])
        rank = (a["has_gps"], a["delay_seconds"] is not None)
        if k not in best or rank > best[k][0]:
            best[k] = (rank, a)
    arrivals = sorted((a for _, a in best.values()), key=lambda a: a["predicted_arrival"])[:limit]

    return {
        "stop": dict(stop),
        "arrivals": arrivals,
        "server_time": now,
        "last_service": last_service,
    }


@app.get("/route/{route_id}/reliability")
def route_reliability(route_id: str):
    return _cached(("rr", route_id), 900, lambda: _route_reliability(route_id))


def _route_reliability(route_id: str):
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
    return _cached(("sr", stop_id), 900, lambda: _stop_reliability(stop_id))


def _stop_reliability(stop_id: str):
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
    from_stop: str = Query(..., alias="from", max_length=20),
    to_stop: str = Query(..., alias="to", max_length=20),
    arrive_by: str | None = Query(None, description="HH:MM 24h, today"),
    limit: int = Query(5, ge=1, le=20),
):
    """
    Direct trips (no transfers) from `from` to `to`, latest departure first.
    Each option includes scheduled times and the typical delay observed for that
    route at the destination's arrival hour.
    """
    if arrive_by is not None and not HHMM.match(arrive_by):
        raise HTTPException(422, "arrive_by must be HH:MM")
    latest = arrive_by + ":00" if arrive_by else _hms(LATEST_GTFS_SECONDS)
    if len(latest) == 7:
        latest = "0" + latest  # 9:30:00 -> 09:30:00 so text comparison works
    today = datetime.now().date()

    with get_conn() as conn:
        filtered = _services_on(conn, today, "svc_day")
        cal_filter = "AND t.service_id IN (SELECT service_id FROM svc_day)" if filtered else ""
        params = [from_stop, to_stop, latest, limit]
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
              AND st.arrival_time <= ?
            ORDER BY sf.departure_time DESC
            LIMIT ?
        """, params).fetchall()
        options = [dict(r) for r in rows]

        # Annotate each option with typical delay at arrival hour for that route
        for o in options:
            hr = _parse_hms(o["arrive_at"]) // 3600 % 24
            d = _cached(("plan", o["route_id"], hr), 1800, lambda: conn.execute("""
                SELECT ROUND(AVG(delay_seconds),0) AS avg_delay, COUNT(*) AS samples
                FROM realtime_updates
                WHERE route_id = ?
                  AND CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) = ?
                  AND delay_seconds IS NOT NULL AND ABS(delay_seconds) <= 7200 AND has_gps = 1
            """, (o["route_id"], hr)).fetchone())
            o["typical_delay_sec"] = int(d["avg_delay"]) if d["avg_delay"] is not None else None
            o["delay_samples"] = d["samples"]

    return {
        "from": from_stop, "to": to_stop, "arrive_by": arrive_by,
        "service_day_filter": filtered,
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
        for tbl in ("routes", "stops", "trips", "stop_times"):
            counts[tbl] = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()["n"]
        # COUNT(*) scans every row (minutes at 14M+ rows). MAX(rowid) is instant and
        # equals the count while nothing is deleted (raw rows are kept on purpose).
        counts["realtime_updates"] = conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) AS n FROM realtime_updates"
        ).fetchone()["n"]
    return counts
