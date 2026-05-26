"""
Heuristic backfill: mark old rows as has_gps=1 when predicted != scheduled
(can only happen if the feed actually adjusted the prediction with real GPS).
Schedule-echo rows stay has_gps=0.
"""
from .db import get_conn, init_db


def run():
    init_db()
    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM realtime_updates WHERE has_gps = 0"
        ).fetchone()["n"]
        print(f"{before:,} rows currently has_gps=0")

        cur = conn.execute("""
            UPDATE realtime_updates
            SET has_gps = 1
            WHERE has_gps = 0
              AND predicted_arrival IS NOT NULL
              AND scheduled_arrival IS NOT NULL
              AND predicted_arrival != scheduled_arrival
        """)
        # cur.rowcount is wrong on some SQLite builds for UPDATE, recount
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM realtime_updates WHERE has_gps = 1"
        ).fetchone()["n"]
        print(f"Now {after:,} rows have has_gps=1 (real GPS observations)")


if __name__ == "__main__":
    run()
