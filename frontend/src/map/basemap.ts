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
//
// swiftcamp's street archive is CONUS only; most of GOES-18's disk is Pacific.
// A zoom 0-6 world extract of the same Protomaps schema, built by
// satweather-refdata (satweather.basemap) and served from /media/basemap/, is
// drawn first: the street layers paint over it wherever they have tiles.
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

type WorldManifest = { world?: { file: string; maxzoom: number } };

async function loadWorld(): Promise<WorldManifest["world"] | null> {
  try {
    const r = await fetch("/media/basemap/manifest.json", { cache: "no-cache" });
    return r.ok ? ((await r.json()) as WorldManifest).world ?? null : null;
  } catch {
    return null; // not built yet: the map still works, CONUS only
  }
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

// The vector layers for one source; `p` prefixes ids so the world and
// street copies can coexist. Hillshade only goes with the street set.
function vectorLayers(src: string, p: string, hillshade: boolean): LayerSpecification[] {
  return [
    { id: `${p}earth`, type: "fill", source: src, "source-layer": "earth", paint: { "fill-color": C.earth } },
    {
      id: `${p}landuse`,
      type: "fill",
      source: src,
      "source-layer": "landuse",
      minzoom: 8,
      paint: { "fill-color": C.landuse, "fill-opacity": 0.6 },
    },
    ...(hillshade ? [{
      id: `${p}hillshade`,
      type: "hillshade",
      source: "terrain",
      paint: {
        "hillshade-exaggeration": 0.35,
        "hillshade-shadow-color": "#05080b",
        "hillshade-highlight-color": "rgba(255,255,255,0.04)",
      },
    } as LayerSpecification] : []),
    { id: `${p}water`, type: "fill", source: src, "source-layer": "water", paint: { "fill-color": C.water } },
    {
      id: `${p}roads-minor`,
      type: "line",
      source: src,
      "source-layer": "roads",
      minzoom: 10,
      filter: ["in", ["get", "kind"], ["literal", ["minor_road", "other"]]],
      paint: { "line-color": C.roadMinor, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.4, 15, 2] },
    },
    {
      id: `${p}roads-major`,
      type: "line",
      source: src,
      "source-layer": "roads",
      minzoom: 5,
      filter: ["in", ["get", "kind"], ["literal", ["highway", "major_road"]]],
      paint: { "line-color": C.roadMajor, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 14, 3] },
    },
    {
      id: `${p}boundaries-state`,
      type: "line",
      source: src,
      "source-layer": "boundaries",
      filter: ["==", ["get", "kind"], "region"],
      paint: { "line-color": C.boundaryState, "line-width": 0.8, "line-dasharray": [3, 2] },
    },
    {
      id: `${p}boundaries-country`,
      type: "line",
      source: src,
      "source-layer": "boundaries",
      filter: ["==", ["get", "kind"], "country"],
      paint: { "line-color": C.boundaryCountry, "line-width": 1.2 },
    },
    {
      id: `${p}places-country`,
      type: "symbol",
      source: src,
      "source-layer": "places",
      filter: ["==", ["get", "kind"], "country"],
      maxzoom: 6,
      layout: { "text-field": ["get", "name"], "text-font": FONT_BOLD, "text-size": 12, "text-transform": "uppercase", "text-letter-spacing": 0.15 },
      paint: { "text-color": C.label, "text-opacity": 0.5, "text-halo-color": C.labelHalo, "text-halo-width": 1 },
    },
    {
      id: `${p}places-region`,
      type: "symbol",
      source: src,
      "source-layer": "places",
      filter: ["==", ["get", "kind"], "region"],
      maxzoom: 8,
      layout: { "text-field": ["get", "name"], "text-font": FONT_BOLD, "text-size": 11, "text-transform": "uppercase", "text-letter-spacing": 0.1 },
      paint: { "text-color": C.label, "text-opacity": 0.55, "text-halo-color": C.labelHalo, "text-halo-width": 1 },
    },
    {
      id: `${p}places-locality`,
      type: "symbol",
      source: src,
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
  const world = await loadWorld();
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
      ...(world ? {
        world: {
          type: "vector" as const,
          url: `pmtiles://${location.origin}/media/basemap/${world.file}`,
          attribution: "© OpenStreetMap contributors",
        },
      } : {}),
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": C.background } },
      ...(world ? vectorLayers("world", "w-", false) : []),
      ...vectorLayers("streets", "", true),
    ],
  };
}

// Both maps stay flat: no tilting into 3D, from a gesture or the compass.
export const FLAT = { maxPitch: 0, pitchWithRotate: false, touchPitch: false } as const;
