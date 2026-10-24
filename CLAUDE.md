# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A weather viewer (https://satweather.808.org) for everything GOES-18 HRIT delivers to the dish on **rtl**: EMWIN text products (parsed, decoded, searchable), warnings, zone forecasts and discussions on a map, METAR/TAF/PIREP decoding, satellite and radar loops, tropical/meso, and the receiver's signal stats.

The site is public through a Cloudflare tunnel (salt `cloudflared` on satweather); LAN clients resolve the same name to the host directly. Everything is public except the originals at `/orig`, which Apache allows only from the LAN (tunnel requests arrive from localhost and get a 403). Frontend code that uses `/orig` must degrade without it: `LoopView`'s deep zoom falls back to the display copy.

The only input is the NFS share that SatDump on rtl writes to (`SATWEATHER_DATA_ROOT`, default `/mnt/satImages/goes-hrit`), mounted read-write here because satweather's housekeeping does all moves and deletes on it. Host setup (venv, systemd units, database, Apache, TLS, the tunnel, `/etc/satweather/satweather.env`) lives in the separate salt repo (`salt/satweather`); this repo only builds a Debian package.

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
make push         # rsync this working tree onto the satweather host, migrate, restart the services
make remote-test  # backend tests on the host against real PostGIS (ARGS="-k imaging" to narrow)
```

`make push` is the fast loop: the host's venv is an editable install of `/opt/satweather/backend`, so new code only needs the rsync and a restart (pip reruns only when `pyproject.toml` changes). Pushed code stays until the next deb from CI replaces it. Salt only reinstalls the package when a newer version is published.

This Mac has no Postgres, so DB tests run on the host: `make remote-test` keeps its own venv in `/var/tmp/satweather-test` and runs pytest as the `postgres` superuser (PostGIS isn't a trusted extension) against the throwaway `satweather_test` database. Non-DB tests (`test_imagery`, `test_emwin`, `test_wxdecode`) also run locally with just sqlalchemy, pydantic-settings, cbor2 and pytest.

`tests/test_store.py` drops and recreates the `public` schema of whatever `SATWEATHER_TEST_DATABASE_URL` points at, then runs migrations — only point it at a disposable PostGIS database. CI uses a `postgis/postgis:17-3.5` service container.

## Releases

Every push to `main` builds `satweather_<yy.m.d.run>_all.deb` in CI (`.github/workflows/publish.yml`) and uploads it to the 808 apt repo; salt installs it with `pkg.latest`. **Never `make publish` or upload a hand-built package** — a colliding version wedges the aptly repo. There is no PR workflow; `publish.yml` still runs the backend tests (against a PostGIS service container) before building.

## Architecture

Four processes (plus a timer) share one PostgreSQL/PostGIS database:

1. **`satweather-ingest`** (`ingest.py`) polls `EMWIN/` (NFS has no cross-client inotify) and ingests files only once they have *settled* (non-empty, unmodified for `SETTLE_S`) — SatDump creates files empty before filling them. One transaction per file; a failing file is recorded with its error and never retried. The same loop catalogs satellite PNGs and LRIT charts under `IMAGES/` (`imagery.Scanner`), and each EMWIN graphic as it is ingested (radar mosaics `RADxxxxx`, NWS maps; `imagery.from_emwin`), into `image_frame` as `pending`. `image_frame.source` is the satellite or `NWS`; `--graphics-backfill DAYS` catalogs EMWIN graphics ingested earlier.
2. **`satweather-imaging`** (`imaging.py`) claims pending `image_frame` rows newest-first with `FOR UPDATE SKIP LOCKED` (the row is the job; a crash rolls the claim back) and writes WebP display copies (≤2048 px) + thumbnails with pyvips into `media_root`, served by Apache at `/media`. Originals byte-identical to an earlier one of the same product become `dup` (LRIT charts are re-sent hourly); EMWIN originals are found in rtl's archive once moved (`imaging.locate`). Needs libvips from apt (salt); its unit is memory-capped.
3. **FastAPI** (`api/app.py`, uvicorn on 127.0.0.1:8400). Apache serves the built frontend and proxies `/api`.
4. **`satweather-housekeep`** (`housekeeping.py`, oneshot every 10 min) is the only thing that moves or deletes on the share; rtl only writes (unas exports it all_squash, so every client's files are 977:988). It archives EMWIN files older than `emwin_archive_hours` to `EMWIN/archive/<UTC date of mtime>/` (only ones ingest has recorded), deletes originals older than `keep_originals_days` (0 = keep forever; only SatDump scan dirs, LRIT charts and archive days whose names parse), and drops `image_frame` rows + WebPs older than `keep_copies_days`. `--dry-run` reports only. Its unit is the only one with the share in `ReadWritePaths`.
- **`satweather-signal`** (`signal.py`) polls the receiver's SatDump stats (`satdump live … --http_server`, JSON at `/api`: `psk_demod` snr/peak_snr/freq, `ccsds_conv_concat_decoder` viterbi_ber/viterbi_lock/deframer_lock/rs_avg) every 10 s into `signal_sample` (`ok=false` when it didn't answer) and pg_notifies each sample; `SATWEATHER_SIGNAL_URL` and `SATWEATHER_RECEIVER` (the receiver's satdump pillar, as JSON) come from salt. `/api/signal` serves the latest/last-minute samples, bucketed history and files per hour for the Signal tab.
5. **`satweather-refdata`** loads NWS zone/county shapefiles (discovered by date-coded name from weather.gov/gis, simplified on load) and the global station list from aviationweather.gov. UGC codes and station IDs only become map geometry via these tables. It also builds the basemap's world tier (see Frontend).

Ingest pipeline: `emwin.py` (filename grammar → `EmwinName`; text normalization, latin-1, injected AWIPS line for international products) → `parse.py` (pyiem NWS parsers → `Parsed`) → `reports.py` (splits `=`-terminated METAR/TAF/PIREP collectives and decodes each with `wxdecode`) → `store.py` (inserts + `pg_notify('satweather', …)` inside the same transaction). `catalog.py` maps AWIPS NNN codes to human names and UI categories; unlisted types still ingest under "other".

Imagery layout on the share: `IMAGES/<GOES-18|GOES-19>/<Full Disk|Mesoscale 1|Mesoscale 2>/<YYYY-MM-DD_HH-MM-SS UTC>/` and `IMAGES/Himawari/<scan>/` (no sector level). Files are bands (`G18_13_<t>Z.png`) or SatDump RGB composites (`abi_rgb_<Name>.png`, plus a `_map.png` variant with borders drawn in); `product.cbor` holds the geostationary projection (stored on each row for later reprojection). A scan directory fills over minutes, so files are catalogued one by one as they settle. The share keeps only a few hours; loops come from the copies in `media/`.

PIREPs are stored at their filing station (`approx`), then `pireps.place_pending` (run from the ingest loop) moves each to its `/OV` position: radial/distance, offset (`15 SW FAT`), over-a-point, route midpoint (stays approx) or lat/lon. Identifiers resolve through Tachbase's anonymous `lookup_waypoints` (`tachbase_api_url`), cached in `waypoint` for a NASR cycle (misses for a day), with our station table for small airports with no ICAO id; radials are magnetic, so WMM declination (pygeomag) is added. Unreachable Tachbase → retried up to `MAX_TRIES`. `reports.py` counts TB/IC as a hazard only with an intensity (not `NEG`/`SMTH`).

Tropical (`tropical.py`, `api/tropical.py`): advisory-package products (TCM/TCP/TCD/PWS/TCA…) get `text_product.storm_id` from the ATCF id in their header; each TCM is parsed into `tc_advisory` (center, motion, pressure, winds, 34/50/64 kt quadrant radii, previous position, forecast/outlook points) exactly as written — no cone, which isn't in the text. TWOs are parsed per basin at request time. Meso sector footprints come from inverting each frame's stored geos projection (`imagery.footprint`, pyproj), which is how a storm knows which meso sector is on it. `--redecode` re-parses stored TCMs.

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

Frontend: Vite + React 19 + react-router 7 + MapLibre + OpenSeadragon. `/` is the landing page (`LandingView`: what this is and how the data gets here, with a live line from the API; its hero image and the self-hosted Archivo font, OFL, are in `frontend/public/`); the Live dashboard is `/live`. `MapView` and `LoopView` are lazy-loaded since those libraries dominate the bundle. `LoopView` (Satellite tab, `group="satellite"`; Radar & charts tab, `group="nws"`, each route keyed so it gets its own instance) creates its OpenSeadragon viewer once and puts every loop frame in its world as a preloaded image, playing by switching opacity; it uses the canvas drawer because the WebGL one aliases a downscaled single image. Deep zoom has no tiles: paused and zoomed past a ≤2048 px display copy, `LoopView` lays the frame's original from `/orig` (Apache's alias for the share; `imagery.orig_url`, satellites only) exactly over the copy, and drops it on play. `StatusBadge` compares the running hashed entry script with `/index.html` every minute and turns into a reload button after a deploy, so an open tab never calls a changed API with old code. Clicking anything on the map opens the right-hand `Inspector` rather than navigating (`?product=` / `?station=` / `?at=lon,lat` deep-link). A click on no feature opens the zone panel (`/api/zones/at`): the public/marine/offshore zone at that point, its latest ZFP/CWF/NSH/OFF segment (reflowed from NWS's 69-column text), hazards in effect there, and other recent products for the zone. Storm-based (polygon) warnings and products count only when the point is inside the polygon, though they are issued per county. To make that discoverable: hovering empty map previews the zone (`/api/zones/at?brief=1`, debounced, skipped while the cursor stays inside the last zone) with an outline and a tooltip, and `PlaceSearch` ("Forecast for…") searches airports, zone names and ZFP "Including the cities of" names (`/api/places`, ranked by distance from the map center; not every office lists cities) or uses the browser's location. The basemap (`map/basemap.ts`) reads swiftcamp's Protomaps archives from `https://cdn.swiftcamp.app` via the pmtiles protocol; archive names change on refresh and must come from `manifest.json`, never be hard-coded. Glyphs in `frontend/public/glyphs` are copied from swiftcamp. swiftcamp's street archive is CONUS-only, so `satweather-refdata` (`basemap.py`) also builds a zoom 0–6 world extract of the Protomaps planet (newest build with the same schema version as swiftcamp's archive; one range request, since a clustered archive keeps low zooms at the front) into `media/basemap/` with a `manifest.json`; `basemap.ts` draws it under the street layers (ids prefixed `w-`) and works without it. Rebuilt every `REBUILD_DAYS`.
