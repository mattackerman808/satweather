// maplibre-gl 6 is ESM-only, with no default export.
import * as maplibregl from "maplibre-gl";
import type { LayerSpecification, StyleSpecification } from "maplibre-gl";
// Bundled builds must say where maplibre's worker is: v6 loads it as its own
// module, and import.meta.url does not find it inside Vite's bundle. Without
// this the map draws its basemap but no GeoJSON layer ever loads.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { Protocol } from "pmtiles";

maplibregl.setWorkerUrl(workerUrl);

// Basemap: swiftcamp's Protomaps archives on R2, read by HTTP range requests
// through the pmtiles protocol. No tile server. Archive names carry build
// dates and change on refresh, so they come from manifest.json at startup and
// are never hard-coded (overwriting an archive in place would corrupt open
// readers, which is why swiftcamp publishes under new names).
export const CDN = "https://cdn.swiftcamp.app";

type Manifest = {
  base: string;
  layers: Record<string, { file: string; minzoom: number; maxzoom: number; tileSize?: number; encoding?: string; attribution?: string }>;
};

let protocolAdded = false;
function ensureProtocol() {
  if (protocolAdded) return;
  maplibregl.addProtocol("pmtiles", new Protocol().tile);
  protocolAdded = true;
}

export async function loadManifest(): Promise<Manifest> {
  const r = await fetch(`${CDN}/manifest.json`);
  if (!r.ok) throw new Error(`manifest ${r.status}`);
  return r.json();
}

// A muted, dark palette: the basemap is the backdrop for imagery, radar and
// warning polygons, so it should never compete with them.
const C = {
  background: "#0b1016",
  earth: "#151c24",
  water: "#0a1a2a",
  landuse: "#18212a",
  roadMinor: "#232d38",
  roadMajor: "#34414f",
  boundaryCountry: "#5b6b7c",
  boundaryState: "#3d4a58",
  label: "#a9b6c4",
  labelHalo: "#0b1016",
};

const FONT_REG = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];

function layers(): LayerSpecification[] {
  return [
    { id: "background", type: "background", paint: { "background-color": C.background } },
    { id: "earth", type: "fill", source: "streets", "source-layer": "earth", paint: { "fill-color": C.earth } },
    {
      id: "landuse",
      type: "fill",
      source: "streets",
      "source-layer": "landuse",
      minzoom: 8,
      paint: { "fill-color": C.landuse, "fill-opacity": 0.6 },
    },
    {
      id: "hillshade",
      type: "hillshade",
      source: "terrain",
      paint: {
        "hillshade-exaggeration": 0.35,
        "hillshade-shadow-color": "#05080b",
        "hillshade-highlight-color": "rgba(255,255,255,0.04)",
      },
    },
    { id: "water", type: "fill", source: "streets", "source-layer": "water", paint: { "fill-color": C.water } },
    {
      id: "roads-minor",
      type: "line",
      source: "streets",
      "source-layer": "roads",
      minzoom: 10,
      filter: ["in", ["get", "kind"], ["literal", ["minor_road", "other"]]],
      paint: { "line-color": C.roadMinor, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.4, 15, 2] },
    },
    {
      id: "roads-major",
      type: "line",
      source: "streets",
      "source-layer": "roads",
      minzoom: 5,
      filter: ["in", ["get", "kind"], ["literal", ["highway", "major_road"]]],
      paint: { "line-color": C.roadMajor, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 14, 3] },
    },
    {
      id: "boundaries-state",
      type: "line",
      source: "streets",
      "source-layer": "boundaries",
      filter: ["==", ["get", "kind"], "region"],
      paint: { "line-color": C.boundaryState, "line-width": 0.8, "line-dasharray": [3, 2] },
    },
    {
      id: "boundaries-country",
      type: "line",
      source: "streets",
      "source-layer": "boundaries",
      filter: ["==", ["get", "kind"], "country"],
      paint: { "line-color": C.boundaryCountry, "line-width": 1.2 },
    },
    {
      id: "places-region",
      type: "symbol",
      source: "streets",
      "source-layer": "places",
      filter: ["==", ["get", "kind"], "region"],
      maxzoom: 8,
      layout: { "text-field": ["get", "name"], "text-font": FONT_BOLD, "text-size": 11, "text-transform": "uppercase", "text-letter-spacing": 0.1 },
      paint: { "text-color": C.label, "text-opacity": 0.55, "text-halo-color": C.labelHalo, "text-halo-width": 1 },
    },
    {
      id: "places-locality",
      type: "symbol",
      source: "streets",
      "source-layer": "places",
      filter: ["==", ["get", "kind"], "locality"],
      minzoom: 5,
      layout: {
        "text-field": ["get", "name"],
        "text-font": FONT_REG,
        "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 12, 14],
        "symbol-sort-key": ["get", "min_zoom"],
      },
      paint: { "text-color": C.label, "text-halo-color": C.labelHalo, "text-halo-width": 1.2 },
    },
  ];
}

export async function basemapStyle(): Promise<StyleSpecification> {
  ensureProtocol();
  const m = await loadManifest();
  const street = m.layers.street;
  const terrain = m.layers.terrain;
  return {
    version: 8,
    glyphs: `${location.origin}/glyphs/{fontstack}/{range}.pbf`,
    sources: {
      streets: {
        type: "vector",
        url: `pmtiles://${m.base}/${street.file}`,
        attribution: street.attribution ?? "© OpenStreetMap contributors",
      },
      terrain: {
        type: "raster-dem",
        url: `pmtiles://${m.base}/${terrain.file}`,
        encoding: (terrain.encoding as "terrarium") ?? "terrarium",
        tileSize: terrain.tileSize ?? 512,
      },
    },
    layers: layers(),
  };
}
