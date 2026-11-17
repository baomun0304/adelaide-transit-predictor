# Adelaide Transit Predictor

[![tests](https://github.com/baomun0304/adelaide-transit-predictor/actions/workflows/ci.yml/badge.svg)](https://github.com/baomun0304/adelaide-transit-predictor/actions/workflows/ci.yml)

Live bus and tram arrivals for Adelaide Metro, with **historical delay patterns** baked in so users know when to actually leave home.

Live: https://adelaide-transit-predictor.vercel.app

<p>
  <img src="docs/screenshots/phone-stop.png" width="260" alt="A stop on a phone: the next bus you can catch, route chips and a leave-by board per route">
</p>

![On a computer the stop panel sits beside the map](docs/screenshots/desktop-stop.png)

Built as a solo learning project. Stack: FastAPI + SQLite + Leaflet, no frameworks on the frontend.

## The problem this solves

Most transit apps show the schedule, or a live prediction if GPS is available. They fail when:

- A bus has no live GPS (common for some buses and most trams): they fall back to the timetable, which is often wrong
- The user doesn't know the route's typical behavior: "this bus is usually 4 min early at 8 AM" is invisible

This app collects realtime delay data 24/7, then uses that history to fill in the gaps for scheduled-only services. The "Be at stop by" recommendation is the headline output.

## What it does

- **Live arrivals**: pulled from the GTFS-Realtime feed every 30 seconds
- **Per-route, per-hour delay patterns**: learned from collected history (interquartile range, not naive average)
- **"Leave by" board**: combines live data, history and your walking time into one time that counts down to Leave now, and says Too late with the next option when you will miss it
- **Next one you can catch**: arrivals grouped by route, with route chips and a saved "my route" per favourite stop
- **Honest data source labels**: "Live GPS", "Operator estimate, no GPS" or "Timetable only", so users aren't misled
- **Per-route reliability**: see how each route behaves at this stop, broken down by morning/midday/evening/night
- **Trip planner API**: direct trips from A to B with typical delay annotation (no screen for it yet)
- **Favourite stops**: saved in browser localStorage
- **Public holidays and late nights**: holiday timetables from calendar_dates, and trips after midnight from the previous service day
- **Mobile-responsive**: works on iPhone

## Tech

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | Async, auto-docs, lightweight |
| Database | SQLite (WAL mode) | Single file, multi-process safe, holds the full realtime history on a 1 GB VM |
| Static feed | GTFS .zip from Adelaide Metro | Industry standard, free, no API key |
| Realtime feed | GTFS-Realtime protobuf | Same standard, refreshed every 30s |
| Frontend | Vanilla HTML/JS + Leaflet (+ markercluster) + Chart.js | No build step, instant loading |
| Map tiles | Esri Light Gray Canvas | Free, no API key, quiet enough for stop markers |
| Hosting | Vercel (frontend), Oracle Cloud VM with systemd (API, collector, weekly timetable reload) | Free tier, runs 24/7 |

## Data sources

All free and public. No API keys.

- GTFS Static: `https://gtfs.adelaidemetro.com.au/v1/static/latest/google_transit.zip`
- GTFS-RT Trip Updates: `https://gtfs.adelaidemetro.com.au/v1/realtime/trip_updates`
- GTFS-RT Vehicle Positions: `https://gtfs.adelaidemetro.com.au/v1/realtime/vehicle_positions`

## Run it yourself

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.gtfs_static                         # download and load the timetable
uvicorn app.main:app --port 8000                  # terminal 1: the API
python -m app.gtfs_realtime                       # terminal 2: the realtime collector
```

Then serve `frontend/` with any static server (for example `python -m http.server 5173` inside `frontend/`). The page talks to the hosted API by default; to use your local one, set `DEFAULT_API` near the top of the script in `frontend/index.html`. Windows helper scripts from the early local setup are in `backend/scripts/`.

## API endpoints

- `GET /stops?lat=&lon=&radius_km=`: stops near a point
- `GET /stops?q=name`: search by name
- `GET /stop/{stop_id}/next`: next arrivals with typical-delay annotations
- `GET /stop/{stop_id}/reliability`: per-hour + per-route breakdown
- `GET /route/{route_id}/reliability`: delay by hour for this route
- `GET /trip/plan?from=&to=&arrive_by=HH:MM`: direct trips A → B
- `GET /routes`, `GET /stats`

Auto-generated docs at `/docs`.

## How the prediction works

1. Collector polls trip updates + vehicle positions every 30 seconds.
2. Each stop-time update gets stored with `has_gps = 1` if the trip has a vehicle position, else `0`.
3. For each arrival query, the app looks up history filtered to:
   - exact hour + same day-type (weekday/weekend): preferred
   - ±1 hour + same day-type: fallback
   - any hour + same day-type: fallback
   - any data: last resort
4. The chosen bucket returns the P10, P25, P50 and P75 of delay (percentiles, robust to outliers). P10 sizes the "be early" buffer, because an early bus is the one you miss completely.
5. Frontend categorises into `on_time / late / early / mixed` and produces wording + a "leave by" time.

Only `has_gps = 1` rows are used for the pattern, schedule-echo rows (no real GPS) are ignored so patterns reflect actual bus behavior, not the timetable.

## Project status

- **Data collected**: around the clock since May 2026. Raw updates are kept on purpose as training data for a later model.
- **Live arrivals**: work immediately, no warm-up needed.
- **Reliability charts**: fill in as data accumulates.
- **Hosting**: frontend on Vercel, API and collector on a small Oracle Cloud VM. Server notes are in [backend/deploy/README.md](backend/deploy/README.md).
- **Tests**: `cd backend && python -m unittest` (API tests on a throwaway database), run by GitHub Actions on every push.

## Deploy

- Frontend: Vercel serves `frontend/` and redeploys on every push to `main`.
- Backend: the API, the collector and a weekly timetable reload run as systemd services on a small Oracle Cloud VM, behind HTTPS. How it is set up, the memory limits that keep a 1 GB machine stable, and how to update it are in [backend/deploy/README.md](backend/deploy/README.md).

## Roadmap

- [x] Phase 1: live tracker
- [x] Phase 2: reliability charts per stop/route
- [x] Phase 3: typical-delay annotations on arrivals
- [x] Phase 4: has_gps flag + honest "Scheduled" labels
- [ ] Phase 5: LightGBM model for sub-minute delay prediction
- [ ] Phase 6: push alerts ("your usual 8:15 bus is 8 min late, leave now")
- [ ] Phase 7: multi-leg trip planning (transfers)
- [ ] Phase 8: weather as a feature

## License

MIT, see [LICENSE](LICENSE). Timetable and realtime data: Adelaide Metro open data. Map tiles: Esri.

## Acknowledgments

Adelaide Metro for publishing open GTFS feeds. ENGR3791 at Flinders University for the kick that started this.
