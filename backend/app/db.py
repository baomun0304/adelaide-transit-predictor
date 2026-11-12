import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    route_id TEXT PRIMARY KEY,
    route_short_name TEXT,
    route_long_name TEXT,
    route_type INTEGER
);

CREATE TABLE IF NOT EXISTS stops (
    stop_id TEXT PRIMARY KEY,
    stop_name TEXT,
    stop_lat REAL,
    stop_lon REAL
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT,
    service_id TEXT,
    trip_headsign TEXT,
    direction_id INTEGER
);

CREATE TABLE IF NOT EXISTS calendar (
    service_id TEXT PRIMARY KEY,
    monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER,
    start_date TEXT, end_date TEXT
);

CREATE TABLE IF NOT EXISTS calendar_dates (
    service_id TEXT,
    date TEXT,
    exception_type INTEGER,
    PRIMARY KEY (service_id, date)
);

-- Filled by cleanup.py when old raw rows are rolled up. Created here too so the API's
-- fallback query never fails on a database that has never been cleaned.
CREATE TABLE IF NOT EXISTS delay_hourly (
    date TEXT, hour INTEGER, dow INTEGER, route_id TEXT, stop_id TEXT,
    avg_delay REAL, min_delay INTEGER, max_delay INTEGER, samples INTEGER,
    PRIMARY KEY (date, hour, route_id, stop_id)
);

CREATE TABLE IF NOT EXISTS stop_times (
    trip_id TEXT,
    arrival_time TEXT,
    departure_time TEXT,
    stop_id TEXT,
    stop_sequence INTEGER,
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id);
CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(trip_id);

CREATE TABLE IF NOT EXISTS realtime_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at INTEGER NOT NULL,
    service_date TEXT NOT NULL,
    trip_id TEXT,
    route_id TEXT,
    stop_id TEXT,
    stop_sequence INTEGER,
    scheduled_arrival INTEGER,
    predicted_arrival INTEGER,
    delay_seconds INTEGER,
    has_gps INTEGER DEFAULT 0,
    UNIQUE(trip_id, stop_id, service_date)
);
CREATE INDEX IF NOT EXISTS idx_rt_stop ON realtime_updates(stop_id);
CREATE INDEX IF NOT EXISTS idx_rt_fetched ON realtime_updates(fetched_at);
CREATE INDEX IF NOT EXISTS idx_rt_route_stop ON realtime_updates(route_id, stop_id);
"""

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(realtime_updates)")]
        if "has_gps" not in cols:
            conn.execute("ALTER TABLE realtime_updates ADD COLUMN has_gps INTEGER DEFAULT 0")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA mmap_size=134217728")  # 128MB memory-map: fewer read syscalls
    conn.execute("PRAGMA cache_size=-4000")      # ~4MB per connection; the OS page cache does the heavy lifting
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
