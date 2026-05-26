"""
One-shot: nullify any delay_seconds outside +/- 2 hours (clearly bogus, mostly
the 24h service-day-boundary bug). Then re-runs the backfill which now picks
the correct service day automatically.
"""
from .db import get_conn, init_db
from .backfill import run as backfill_run

THRESHOLD_SECONDS = 2 * 3600  # 2 hours


def run():
    init_db()
    with get_conn() as conn:
        bad = conn.execute(
            "SELECT COUNT(*) AS n FROM realtime_updates "
            "WHERE delay_seconds IS NOT NULL AND ABS(delay_seconds) > ?",
            (THRESHOLD_SECONDS,),
        ).fetchone()["n"]
        print(f"Nullifying {bad:,} rows with |delay| > 2 hours (bogus)...")
        conn.execute(
            "UPDATE realtime_updates SET delay_seconds = NULL, scheduled_arrival = NULL "
            "WHERE delay_seconds IS NOT NULL AND ABS(delay_seconds) > ?",
            (THRESHOLD_SECONDS,),
        )
    print("Now re-running backfill with corrected service-day logic...")
    backfill_run()


if __name__ == "__main__":
    run()
