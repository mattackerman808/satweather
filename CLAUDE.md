# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A LAN-only weather viewer (https://satweather.808.org) for everything GOES-18 HRIT delivers to the dish on **rtl**: EMWIN text products (parsed, decoded, searchable), warnings on a map, METAR/TAF/PIREP decoding, and (planned) satellite imagery, radar, tropical/meso. Satellite and Tropical pages are still placeholders (phases 3 and 5).

The only input is the read-only NFS share that SatDump on rtl writes to (`SATWEATHER_DATA_ROOT`, default `/mnt/satImages/goes-hrit`). Host setup (venv, systemd units, database, Apache, TLS, `/etc/satweather/satweather.env`) lives in the separate salt repo (`salt/satweather`); this repo only builds a Debian package.

## Commands

```bash
# Backend (Python 3.13)
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest                                   # all tests (DB tests skip without a test DB)
.venv/bin/pytest tests/test_parse.py -k vtec       # single file / test
SATWEATHER_TEST_DATABASE_URL=postgresql+psycopg://user:pw@127.0.0.1/satweather_test .venv/bin/pytest tests/test_store.py
SATWEATHER_DATABASE_URL=postgresql+psycopg://... .venv/bin/uvicorn satweather.api.app:app --port 8400

# Console scripts (from pyproject)
satweather-migrate [--check]       # alembic upgrade head; --check exits 1 if not at head (salt uses it)
satweather-ingest [--once|--backfill|--redecode]
satweather-refdata [--force] [--only <set>|station]

# Frontend (Node 22)
cd frontend && npm ci && npm run dev    # Vite; proxies /api to 127.0.0.1:8400
npm run typecheck                       # tsc -b; there is no frontend linter or test suite
npm run build

# Repo root
make test     # backend pytest
make web      # frontend ci + build
make deb      # build the .deb locally
make push     # rsync this working tree onto the satweather host, migrate, restart API + ingest
```

`make push` is the fast loop: the host's venv is an editable install of `/opt/satweather/backend`, so new code only needs the rsync and a restart (pip reruns only when `pyproject.toml` changes). Pushed code stays until the next deb from CI replaces it. Salt only reinstalls the package when a newer version is published.

`tests/test_store.py` drops and recreates the `public` schema of whatever `SATWEATHER_TEST_DATABASE_URL` points at, then runs migrations — only point it at a disposable PostGIS database. CI uses a `postgis/postgis:17-3.5` service container.

## Releases

Every push to `main` builds `satweather_<yy.m.d.run>_all.deb` in CI (`.github/workflows/publish.yml`) and uploads it to the 808 apt repo; salt installs it with `pkg.latest`. **Never `make publish` or upload a hand-built package** — a colliding version wedges the aptly repo. There is no PR workflow; `publish.yml` still runs the backend tests (against a PostGIS service container) before building.

## Architecture

Three processes share one PostgreSQL/PostGIS database:

1. **`satweather-ingest`** (`ingest.py`) polls `EMWIN/` (NFS has no cross-client inotify) and ingests files only once they have *settled* (non-empty, unmodified for `SETTLE_S`) — SatDump creates files empty before filling them. One transaction per file; a failing file is recorded with its error and never retried.
2. **FastAPI** (`api/app.py`, uvicorn on 127.0.0.1:8400). Apache serves the built frontend and proxies `/api`.
3. **`satweather-refdata`** loads NWS zone/county shapefiles (discovered by date-coded name from weather.gov/gis, simplified on load) and the global station list from aviationweather.gov. UGC codes and station IDs only become map geometry via these tables.

Ingest pipeline: `emwin.py` (filename grammar → `EmwinName`; text normalization, latin-1, injected AWIPS line for international products) → `parse.py` (pyiem NWS parsers → `Parsed`) → `reports.py` (splits `=`-terminated METAR/TAF/PIREP collectives and decodes each with `wxdecode`) → `store.py` (inserts + `pg_notify('satweather', …)` inside the same transaction). `catalog.py` maps AWIPS NNN codes to human names and UI categories; unlisted types still ingest under "other".

Live updates: `store.py` notifies → `api/events.py` LISTENs on a dedicated psycopg connection per client and streams SSE (`/api/events`) → `frontend/src/events.ts` keeps one shared `EventSource` for all subscribers.

Key invariants:
- **Parsing never raises.** Unparseable products are stored raw and searchable with `parse_error` set. `test_parse.py` asserts every fixture parses except the `KNOWN_RAW` set.
- **pyiem must always get explicit empty providers** — its defaults query IEM's private database. pyiem is imported lazily because it pulls in pandas/geopandas, which the API process should not load.
- **VTEC activity is tracked per UGC** (`vtec_ugc_state`), since partial cancellations are common. `CAN`/`EXP`/`UPG` end an event for the UGCs they name.
- **`wxdecode.py` and `tests/test_wxdecode.py` are vendored unchanged from Tachbase** (`tachbase-api`, commit in the file header). Don't edit them here: fix upstream, then re-copy, so a plain diff still works.
- After changing decoding, `satweather-ingest --redecode` backfills already-stored products.
- The API uses raw SQL via SQLAlchemy `text()`, not an ORM. Schema changes go in a new Alembic migration under `backend/satweather/db/migrations/versions/` (numbered `000N_*.py`); the Alembic config ships inside the package so `satweather-migrate` works from any directory.
- Settings come from `SATWEATHER_*` env vars (`config.py`, cached via `lru_cache`; tests call `get_settings.cache_clear()` / `get_engine.cache_clear()` after changing env).

Test fixtures in `backend/tests/fixtures/emwin/` are real EMWIN files, roughly one per product type seen; add one when supporting a new type.

Frontend: Vite + React 19 + react-router 7 + MapLibre. `MapView` is lazy-loaded since MapLibre dominates the bundle. Clicking anything on the map opens the right-hand `Inspector` rather than navigating (`?product=` / `?station=` deep-link). The basemap (`map/basemap.ts`) reads swiftcamp's Protomaps archives from `https://cdn.swiftcamp.app` via the pmtiles protocol; archive names change on refresh and must come from `manifest.json`, never be hard-coded. Glyphs in `frontend/public/glyphs` are copied from swiftcamp.
