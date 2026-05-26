"""
Aggregate old realtime_updates into hourly summaries, then delete raw rows.

Keeps raw rows for the last N days (default 7) for fine-grained queries.
Everything older becomes one row per (route, stop, hour-of-day, date) — enough
for reliability charts and ML training, ~1000x smaller.
"""
import time
import argparse
from .db import get_conn, init_db


AGGREGATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS delay_hourly (
    date TEXT,
    hour INTEGER,
    dow INTEGER,
    route_id TEXT,
    stop_id TEXT,
    avg_delay REAL,
    min_delay INTEGER,
    max_delay INTEGER,
    samples INTEGER,
    PRIMARY KEY (date, hour, route_id, stop_id)
);
CREATE INDEX IF NOT EXISTS idx_dh_route ON delay_hourly(route_id);
CREATE INDEX IF NOT EXISTS idx_dh_stop  ON delay_hourly(stop_id);
"""


def run(keep_days: int = 7):
    init_db()
    cutoff = int(time.time()) - keep_days * 86400
    with get_conn() as conn:
        conn.executescript(AGGREGATE_SCHEMA)

        before = conn.execute(
            "SELECT COUNT(*) AS n FROM realtime_updates WHERE fetched_at < ?",
            (cutoff,),
        ).fetchone()["n"]
        if before == 0:
            print(f"Nothing to aggregate (keep_days={keep_days}).")
            return

        print(f"Aggregating {before:,} rows older than {keep_days} days...")

        conn.execute("""
            INSERT OR REPLACE INTO delay_hourly
            SELECT
              date(fetched_at, 'unixepoch', 'localtime')                          AS date,
              CAST(strftime('%H', datetime(fetched_at,'unixepoch','localtime')) AS INT) AS hour,
              CAST(strftime('%w', datetime(fetched_at,'unixepoch','localtime')) AS INT) AS dow,
              route_id, stop_id,
              ROUND(AVG(delay_seconds), 1),
              MIN(delay_seconds), MAX(delay_seconds),
              COUNT(*)
            FROM realtime_updates
            WHERE fetched_at < ? AND delay_seconds IS NOT NULL
            GROUP BY date, hour, route_id, stop_id
        """, (cutoff,))

        conn.execute(
            "DELETE FROM realtime_updates WHERE fetched_at < ?", (cutoff,)
        )
        conn.execute("VACUUM")

    print("Done. delay_hourly populated, old raw rows removed, DB compacted.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--keep-days", type=int, default=7)
    args = p.parse_args()
    run(args.keep_days)
