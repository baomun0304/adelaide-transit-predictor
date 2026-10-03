"""One-off: covering indexes so the reliability / typical-delay queries read
straight from the index instead of random-accessing a multi-GB table (a busy
stop took ~100s). Output is unchanged, only the access path is.

Building holds the DB write lock for minutes, so run it only in the collector's
quiet hours (01:00-05:00):  python -m app.add_indexes
Safe to re-run (IF NOT EXISTS). Roll back with DROP INDEX.
"""
import sqlite3
import time
from datetime import datetime
from .config import DB_PATH
from .gtfs_realtime import is_quiet_hours

INDEXES = {
    "idx_rt_cov_route": "route_id, stop_id, fetched_at, delay_seconds, has_gps",
    "idx_rt_cov_stop": "stop_id, route_id, fetched_at, delay_seconds, has_gps",
}

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH, timeout=600)
    conn.execute("PRAGMA busy_timeout=600000")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-16000")
    for name, cols in INDEXES.items():
        if not is_quiet_hours():
            print(f"{datetime.now():%H:%M:%S} outside quiet hours, not starting {name}")
            break
        t = time.time()
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON realtime_updates({cols})")
        conn.commit()
        print(f"{datetime.now():%H:%M:%S} {name} ready in {time.time() - t:.0f}s", flush=True)
    conn.close()
