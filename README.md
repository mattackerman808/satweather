# satweather

A weather viewer for everything GOES-18 HRIT delivers to rtl's dish:
satellite imagery (Full Disk, Mesoscale 1/2, L2), EMWIN text products
(decoded and searchable), radar mosaics, warnings on a map, and a tropical
and mesoscale portal. LAN-only at https://satweather.808.org.

## How it fits together

- **rtl** (Pi 5 + dish) runs SatDump and writes `goes-hrit/` over NFS to a
  directory that satweather exports from its data disk. That is the only input.
- **satweather** (bespin VM) reads it locally. Ingest workers parse
  text products into PostgreSQL/PostGIS and build web-sized images on local
  disk (`/srv/satweather`). A FastAPI app serves the API; Apache serves the
  React UI and proxies `/api`.
- **Basemap**: swiftcamp's Protomaps archives on R2
  (`https://cdn.swiftcamp.app`, archive names from its `manifest.json`), read
  with the pmtiles protocol. Glyphs are copied from swiftcamp into
  `frontend/public/glyphs`.
- The host side (venv, units, database, Apache, TLS) lives in the salt repo,
  `salt/satweather`. This repo only builds the package.

## Layout

```
backend/satweather/   FastAPI app (api/), DB + Alembic migrations (db/)
frontend/             Vite + React + TypeScript + MapLibre
packaging/            Debian control file
```

## Development

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest
SATWEATHER_DATABASE_URL=postgresql+psycopg://... .venv/bin/uvicorn satweather.api.app:app --port 8400

cd frontend && npm ci && npm run dev     # proxies /api to :8400
```

## Releases

Every push to `main` builds `satweather_<yy.m.d.run>_all.deb` in CI and
uploads it to the 808 apt repo; salt installs it with `pkg.latest`. Never
upload a hand-built package.
