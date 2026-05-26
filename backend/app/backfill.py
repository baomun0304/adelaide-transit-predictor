"""
One-shot backfill: compute delay_seconds for old realtime_updates rows
that pre-date the parser fix.

Uses fetched_at to determine the service date, then looks up the scheduled
arrival time from stop_times by (trip_id, stop_id).
"""
import time
from datetime import datetime, timedelta
from .db import get_conn, init_db


def _parse_hms(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _midnight_epoch(unix_ts: int) -> int:
    dt = datetime.fromtimestamp(unix_ts)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


def _best_sched(hms: str, midnight_ts: int, predicted: int) -> int:
    base = midnight_ts + _parse_hms(hms)
    return min((base - 86400, base, base + 86400),
               key=lambda s: abs(predicted - s))


def run(batch_size: int = 50000):
    init_db()
    with get_conn() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_st_trip_stop ON stop_times(trip_id, stop_id)")
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM realtime_updates "
            "WHERE delay_seconds IS NULL AND predicted_arrival IS NOT NULL"
        ).fetchone()["n"]
    print(f"{total:,} rows to backfill. Starting...")

    processed = 0
    updated = 0
    t0 = time.time()

    while True:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT id, trip_id, stop_id, fetched_at, predicted_arrival
                FROM realtime_updates
                WHERE delay_seconds IS NULL AND predicted_arrival IS NOT NULL
                LIMIT ?
            """, (batch_size,)).fetchall()

            if not rows:
                break

            # Bulk-fetch scheduled times for all (trip_id, stop_id) pairs in this batch
            pairs = list({(r["trip_id"], r["stop_id"]) for r in rows})
            sched = {}
            for i in range(0, len(pairs), 400):
                chunk = pairs[i:i+400]
                placeholders = ",".join(["(?,?)"] * len(chunk))
                flat = [v for p in chunk for v in p]
                for s in conn.execute(f"""
                    SELECT trip_id, stop_id, arrival_time FROM stop_times
                    WHERE (trip_id, stop_id) IN ({placeholders})
                """, flat).fetchall():
                    sched[(s["trip_id"], s["stop_id"])] = s["arrival_time"]

            updates = []
            for r in rows:
                key = (r["trip_id"], r["stop_id"])
                if key not in sched:
                    continue
                midnight = _midnight_epoch(r["fetched_at"])
                sched_eps = _best_sched(sched[key], midnight, r["predicted_arrival"])
                delay = r["predicted_arrival"] - sched_eps
                updates.append((sched_eps, delay, r["id"]))

            if updates:
                conn.executemany(
                    "UPDATE realtime_updates SET scheduled_arrival=?, delay_seconds=? WHERE id=?",
                    updates,
                )
                updated += len(updates)

            processed += len(rows)

        rate = processed / max(1, time.time() - t0)
        eta = (total - processed) / max(1, rate)
        print(f"  processed {processed:,}/{total:,}  "
              f"updated {updated:,}  rate {int(rate):,}/s  eta {int(eta)}s")

    print(f"Done. Updated {updated:,} rows.")


if __name__ == "__main__":
    run()
