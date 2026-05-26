import csv
import zipfile
import requests
from .config import GTFS_STATIC_URL, GTFS_ZIP, GTFS_EXTRACT
from .db import get_conn, init_db


def download():
    print(f"Downloading GTFS from {GTFS_STATIC_URL}")
    r = requests.get(GTFS_STATIC_URL, timeout=60)
    r.raise_for_status()
    GTFS_ZIP.write_bytes(r.content)
    print(f"Saved {GTFS_ZIP} ({len(r.content)//1024} KB)")

    GTFS_EXTRACT.mkdir(exist_ok=True)
    with zipfile.ZipFile(GTFS_ZIP) as zf:
        zf.extractall(GTFS_EXTRACT)
    print(f"Extracted to {GTFS_EXTRACT}")


def _rows(filename):
    path = GTFS_EXTRACT / filename
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            yield row


def load():
    init_db()
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("DELETE FROM routes")
        cur.executemany(
            "INSERT INTO routes VALUES (?,?,?,?)",
            [(r["route_id"], r.get("route_short_name"), r.get("route_long_name"),
              int(r["route_type"]) if r.get("route_type") else None)
             for r in _rows("routes.txt")],
        )

        cur.execute("DELETE FROM stops")
        cur.executemany(
            "INSERT INTO stops VALUES (?,?,?,?)",
            [(r["stop_id"], r["stop_name"],
              float(r["stop_lat"]) if r["stop_lat"] else None,
              float(r["stop_lon"]) if r["stop_lon"] else None)
             for r in _rows("stops.txt")],
        )

        cur.execute("DELETE FROM trips")
        cur.executemany(
            "INSERT INTO trips VALUES (?,?,?,?,?)",
            [(r["trip_id"], r["route_id"], r["service_id"],
              r.get("trip_headsign"),
              int(r["direction_id"]) if r.get("direction_id") else None)
             for r in _rows("trips.txt")],
        )

        cur.execute("DELETE FROM calendar")
        try:
            cur.executemany(
                "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(r["service_id"],
                  int(r.get("monday", 0)), int(r.get("tuesday", 0)),
                  int(r.get("wednesday", 0)), int(r.get("thursday", 0)),
                  int(r.get("friday", 0)), int(r.get("saturday", 0)),
                  int(r.get("sunday", 0)),
                  r.get("start_date"), r.get("end_date"))
                 for r in _rows("calendar.txt")],
            )
        except FileNotFoundError:
            print("calendar.txt missing - service-day filter will be disabled")

        cur.execute("DELETE FROM stop_times")
        batch = []
        for r in _rows("stop_times.txt"):
            batch.append((
                r["trip_id"], r["arrival_time"], r["departure_time"],
                r["stop_id"], int(r["stop_sequence"]),
            ))
            if len(batch) >= 5000:
                cur.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            cur.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?)", batch)

    print("GTFS static loaded into SQLite")


if __name__ == "__main__":
    download()
    load()
