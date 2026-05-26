from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "transit.db"
GTFS_ZIP = DATA_DIR / "google_transit.zip"
GTFS_EXTRACT = DATA_DIR / "gtfs_static"

GTFS_STATIC_URL = "https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip"
GTFS_REALTIME_TRIP_UPDATES = "https://gtfs.adelaidemetro.com.au/v1/realtime/trip_updates"
GTFS_REALTIME_VEHICLE_POSITIONS = "https://gtfs.adelaidemetro.com.au/v1/realtime/vehicle_positions"

POLL_INTERVAL_SECONDS = 30
