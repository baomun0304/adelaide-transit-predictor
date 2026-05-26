import time
from datetime import datetime
import requests
from google.transit import gtfs_realtime_pb2
from .config import (GTFS_REALTIME_TRIP_UPDATES, GTFS_REALTIME_VEHICLE_POSITIONS,
                     POLL_INTERVAL_SECONDS)
from .db import get_conn, init_db

QUIET_START_HOUR = 1
QUIET_END_HOUR = 5


def is_quiet_hours(now=None):
    h = (now or datetime.now()).hour
    return QUIET_START_HOUR <= h < QUIET_END_HOUR


def fetch_trip_updates():
    r = requests.get(GTFS_REALTIME_TRIP_UPDATES, timeout=30)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    return feed


def fetch_live_trip_ids():
    """Trip IDs that have a current vehicle position - i.e. actual GPS data."""
    try:
        r = requests.get(GTFS_REALTIME_VEHICLE_POSITIONS, timeout=30)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        live = set()
        for entity in feed.entity:
            if entity.HasField("vehicle"):
                tid = entity.vehicle.trip.trip_id
                if tid:
                    live.add(tid)
        return live
    except Exception as e:
        print(f"vehicle_positions fetch failed: {e}")
        return set()


def _scheduled_epoch(hms: str, midnight_ts: int) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return midnight_ts + h * 3600 + m * 60 + s


def _best_scheduled_epoch(hms: str, midnight_ts: int, predicted: int) -> int:
    """
    Adelaide's service day can run past midnight, and the trip's service
    date may be yesterday or tomorrow relative to fetched_at. Try ±1 day and
    pick the scheduled epoch closest to the predicted arrival.
    """
    base = _scheduled_epoch(hms, midnight_ts)
    return min((base - 86400, base, base + 86400),
               key=lambda s: abs(predicted - s))


def store_updates(feed, live_trip_ids=None):
    if live_trip_ids is None:
        live_trip_ids = set()
    now = int(time.time())
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_ts = int(today.timestamp())

    # First pass: collect raw rows
    raw = []
    need_scheduled = set()  # (trip_id, stop_sequence)
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        route_id = tu.trip.route_id
        for stu in tu.stop_time_update:
            stop_id = stu.stop_id
            seq = stu.stop_sequence if stu.HasField("stop_sequence") else None

            predicted = None
            delay = None
            if stu.HasField("arrival"):
                if stu.arrival.time:
                    predicted = stu.arrival.time
                if stu.arrival.HasField("delay"):
                    delay = stu.arrival.delay
            if stu.HasField("departure"):
                if predicted is None and stu.departure.time:
                    predicted = stu.departure.time
                if delay is None and stu.departure.HasField("delay"):
                    delay = stu.departure.delay

            if delay is None and predicted is not None:
                need_scheduled.add((trip_id, stop_id))

            has_gps = 1 if trip_id in live_trip_ids else 0
            raw.append([now, trip_id, route_id, stop_id, seq,
                        None, predicted, delay, has_gps])

    # Second pass: bulk-lookup scheduled times for rows missing delay
    sched_lookup = {}
    if need_scheduled:
        with get_conn() as conn:
            # SQLite limit: 999 params. Chunk it.
            items = list(need_scheduled)
            for i in range(0, len(items), 400):
                chunk = items[i:i+400]
                placeholders = ",".join(["(?,?)"] * len(chunk))
                flat = [v for pair in chunk for v in pair]
                rows = conn.execute(f"""
                    SELECT trip_id, stop_id, arrival_time
                    FROM stop_times
                    WHERE (trip_id, stop_id) IN ({placeholders})
                """, flat).fetchall()
                for r in rows:
                    sched_lookup[(r["trip_id"], r["stop_id"])] = r["arrival_time"]

    # Fill in computed delays + scheduled epoch
    for row in raw:
        trip_id = row[1]
        stop_id = row[3]
        predicted, delay = row[6], row[7]
        key = (trip_id, stop_id)
        if key in sched_lookup and predicted is not None:
            sched_eps = _best_scheduled_epoch(sched_lookup[key], midnight_ts, predicted)
            row[5] = sched_eps
            if delay is None:
                row[7] = predicted - sched_eps

    if not raw:
        return 0
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO realtime_updates
            (fetched_at, trip_id, route_id, stop_id, stop_sequence,
             scheduled_arrival, predicted_arrival, delay_seconds, has_gps)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, raw)
    return len(raw)


def poll_loop():
    init_db()
    while True:
        if is_quiet_hours():
            print(f"[{time.strftime('%H:%M:%S')}] quiet hours, sleeping 5 min")
            time.sleep(300)
            continue
        try:
            feed = fetch_trip_updates()
            live_ids = fetch_live_trip_ids()
            n = store_updates(feed, live_ids)
            print(f"[{time.strftime('%H:%M:%S')}] stored {n} updates ({len(live_ids)} with live GPS)")
        except Exception as e:
            print(f"error: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    poll_loop()
