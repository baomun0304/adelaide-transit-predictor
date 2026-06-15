"""
One-shot migration: collapse the bloated realtime_updates table.

Old design inserted a new row every 30s per (trip, stop) -> ~60x redundant
snapshots, plus ~96% of rows are schedule-echoes (has_gps=0) that patterns
never use.

New design: one row per (trip, stop, service_date), holding the final
observed values. This migration rebuilds the table keeping:
  - all of today's rows (so /next keeps working immediately)
  - all historical has_gps=1 rows, deduped to the latest poll per trip/stop/day

Then drops the old table and VACUUMs to reclaim disk.

RUN WITH COLLECTOR AND API STOPPED.
"""
import time
import sqlite3
from .config import DB_PATH


NEW_SCHEMA = """
CREATE TABLE realtime_updates_new (
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
"""

COPY = """
INSERT INTO realtime_updates_new
  (fetched_at, service_date, trip_id, route_id, stop_id, stop_sequence,
   scheduled_arrival, predicted_arrival, delay_seconds, has_gps)
SELECT fetched_at,
       date(fetched_at,'unixepoch','localtime') AS sd,
       trip_id, route_id, stop_id, stop_sequence,
       scheduled_arrival, predicted_arrival, delay_seconds, has_gps
FROM realtime_updates
WHERE has_gps = 1
   OR date(fetched_at,'unixepoch','localtime') = date('now','localtime')
ON CONFLICT(trip_id, stop_id, service_date) DO UPDATE SET
  fetched_at=excluded.fetched_at,
  route_id=excluded.route_id,
  stop_sequence=excluded.stop_sequence,
  scheduled_arrival=COALESCE(excluded.scheduled_arrival, scheduled_arrival),
  predicted_arrival=excluded.predicted_arrival,
  delay_seconds=COALESCE(excluded.delay_seconds, delay_seconds),
  has_gps=MAX(has_gps, excluded.has_gps)
WHERE excluded.fetched_at >= realtime_updates_new.fetched_at;
"""

INDEXES = [
    "CREATE INDEX idx_rt_stop ON realtime_updates(stop_id)",
    "CREATE INDEX idx_rt_fetched ON realtime_updates(fetched_at)",
    "CREATE INDEX idx_rt_route_stop ON realtime_updates(route_id, stop_id)",
]


def run():
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")

    print("Checkpointing WAL...")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    before = conn.execute("SELECT COUNT(*) FROM realtime_updates").fetchone()[0]
    print(f"Old table: {before:,} rows")

    print("Dropping any partial new table from a previous run...")
    conn.execute("DROP TABLE IF EXISTS realtime_updates_new")
    conn.executescript(NEW_SCHEMA)

    print("Copying + deduping keepers (this is the slow part, scans the old table)...")
    t0 = time.time()
    conn.execute(COPY)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM realtime_updates_new").fetchone()[0]
    print(f"New table: {after:,} rows  ({before/max(1,after):.0f}x smaller)  in {int(time.time()-t0)}s")

    print("Swapping tables...")
    conn.execute("DROP TABLE realtime_updates")
    conn.execute("ALTER TABLE realtime_updates_new RENAME TO realtime_updates")

    print("Rebuilding indexes...")
    for ix in INDEXES:
        conn.execute(ix)
    conn.commit()

    print("VACUUM (reclaims disk, may take a minute)...")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.close()
    print("Done. Check data/ folder size now.")


if __name__ == "__main__":
    run()
