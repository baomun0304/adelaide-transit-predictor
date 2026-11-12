"""API tests against a small throwaway database. Run from backend/: python -m unittest"""
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from app import db

TMP = Path(tempfile.mkdtemp()) / "test.db"
db.DB_PATH = TMP  # get_conn and init_db read this at call time

from fastapi.testclient import TestClient  # noqa: E402
from app import main  # noqa: E402


def _hms(dt_secs):
    return f"{dt_secs // 3600:02d}:{dt_secs % 3600 // 60:02d}:{dt_secs % 60:02d}"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        today = datetime.now().date()
        ymd = today.strftime("%Y%m%d")
        dow = main.DOW_COLS[(today.weekday() + 1) % 7]
        in_ten = int(time.time()) - main._service_midnight(today) + 600
        with db.get_conn() as conn:
            conn.execute("INSERT INTO stops VALUES ('S1', 'Rundle St 100%_Stop', -34.92, 138.60)")
            conn.execute("INSERT INTO stops VALUES ('S2', 'King William St', -34.93, 138.60)")
            conn.execute("INSERT INTO routes VALUES ('R1', '99C', 'City Loop', 3)")
            days = {d: int(d == dow) for d in main.DOW_COLS}
            cols = [days[d] for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
            conn.execute("INSERT INTO calendar VALUES ('RUNS', ?,?,?,?,?,?,?, '20000101', '21000101')", cols)
            conn.execute("INSERT INTO calendar VALUES ('HOLIDAY_OFF', ?,?,?,?,?,?,?, '20000101', '21000101')", cols)
            conn.execute("INSERT INTO calendar_dates VALUES ('HOLIDAY_OFF', ?, 2)", (ymd,))
            conn.execute("INSERT INTO calendar_dates VALUES ('EXTRA', ?, 1)", (ymd,))
            for trip, service in (("T_RUNS", "RUNS"), ("T_OFF", "HOLIDAY_OFF"), ("T_EXTRA", "EXTRA")):
                conn.execute("INSERT INTO trips VALUES (?, 'R1', ?, 'City', 0)", (trip, service))
                conn.execute("INSERT INTO stop_times VALUES (?, ?, ?, 'S1', 1)", (trip, _hms(in_ten), _hms(in_ten)))
                conn.execute("INSERT INTO stop_times VALUES (?, ?, ?, 'S2', 2)", (trip, _hms(in_ten + 300), _hms(in_ten + 300)))
        cls.client = TestClient(main.app)

    def test_holiday_exceptions_decide_which_trips_run(self):
        trips = {a["trip_id"] for a in self.client.get("/stop/S1/next").json()["arrivals"]}
        self.assertIn("T_RUNS", trips)
        self.assertIn("T_EXTRA", trips, "a service added for today must run")
        self.assertNotIn("T_OFF", trips, "a service removed for today must not run")

    def test_arrival_has_history_fields_even_without_history(self):
        a = self.client.get("/stop/S1/next").json()["arrivals"][0]
        self.assertIsNone(a["typical"])  # empty history, no crash on the rolled-up fallback table
        self.assertEqual(a["route_short_name"], "99C")

    def test_unknown_stop_is_404(self):
        self.assertEqual(self.client.get("/stop/NOPE/next").status_code, 404)

    def test_limits_are_bounded(self):
        self.assertEqual(self.client.get("/stop/S1/next?limit=5000").status_code, 422)
        self.assertEqual(self.client.get("/stops?lat=-34.9&lon=138.6&radius_km=50").status_code, 422)

    def test_search_treats_wildcards_as_text(self):
        names = [s["stop_name"] for s in self.client.get("/stops?q=100%25_").json()]
        self.assertEqual(names, ["Rundle St 100%_Stop"])
        self.assertEqual(self.client.get("/stops?q=%25%25").json(), [])

    def test_trip_plan_validates_time(self):
        self.assertEqual(self.client.get("/trip/plan?from=S1&to=S2&arrive_by=zz").status_code, 422)
        r = self.client.get("/trip/plan?from=S1&to=S2")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({o["trip_id"] for o in r.json()["options"]}, {"T_RUNS", "T_EXTRA"})

    def test_service_midnight_is_noon_minus_twelve_hours(self):
        d = date(2026, 4, 5)  # Adelaide's daylight saving ends on this day
        noon = int(datetime(2026, 4, 5, 12).timestamp())
        self.assertEqual(main._service_midnight(d), noon - 12 * 3600)
        self.assertEqual(main._service_midnight(d + timedelta(days=1)) - main._service_midnight(d) in (86400, 90000, 82800), True)


if __name__ == "__main__":
    unittest.main()
