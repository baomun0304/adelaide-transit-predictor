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
    trip_id TEXT,
    route_id TEXT,
    stop_id TEXT,
    stop_sequence INTEGER,
    scheduled_arrival INTEGER,
    predicted_arrival INTEGER,
    delay_seconds INTEGER,
    has_gps INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rt_trip ON realtime_updates(trip_id);
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
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
