import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle, FLAT } from "../map/basemap";
import { api, type PirepDecoded } from "../api";
import { useLiveEvents } from "../events";
import Inspector, { type Inspect } from "../map/Inspector";
import PlaceSearch from "../map/PlaceSearch";
import { canHover, clearOfSheet, fitPadding } from "../narrow";
import { CAT_COLOR } from "../wx/Wx";

// GOES-18 sits at 137.0°W: open over the western US and the eastern
// Pacific, the part of its disk this site is about.
const START = { center: [-118, 38] as [number, number], zoom: 3.6 };
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Legend = { name: string; color: string; n: number }[];
type Layers = { warnings: boolean; discussions: boolean; stations: boolean; pireps: boolean };
const LAYER_IDS: Record<keyof Layers, string[]> = {
  warnings: ["warnings-fill", "warnings-line"],
  discussions: ["disc-fill", "disc-line", "disc-label"],
  stations: ["stations-dot", "stations-label"],
  pireps: ["pireps-dot"],
};

const catColor = ["match", ["get", "flight_category"],
  "VFR", CAT_COLOR.VFR, "MVFR", CAT_COLOR.MVFR, "IFR", CAT_COLOR.IFR, "LIFR", CAT_COLOR.LIFR, CAT_COLOR.UNK,
] as unknown as maplibregl.ExpressionSpecification;

// Point in a GeoJSON (multi)polygon: inside an outer ring, outside its holes.
function inside([x, y]: [number, number], g: GeoJSON.Geometry): boolean {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  const inRing = (ring: GeoJSON.Position[]) => {
    let c = false;
    for (let a = 0, b = ring.length - 1; a < ring.length; b = a++) {
      const [xa, ya] = ring[a], [xb, yb] = ring[b];
      if ((ya > y) !== (yb > y) && x < ((xb - xa) * (y - ya)) / (yb - ya) + xa) c = !c;
    }
    return c;
  };
  return polys.some(([outer, ...holes]) => inRing(outer) && !holes.some(inRing));
}

// Layers that open something of their own when clicked; elsewhere a click
// opens the zone forecast, and hovering previews which zone.
const FEATURE_LAYERS = ["stations-dot", "pireps-dot", "disc-fill", "warnings-fill"];

function addLayers(map: maplibregl.Map) {
  map.addSource("warnings", { type: "geojson", data: EMPTY });
  map.addSource("focus", { type: "geojson", data: EMPTY });
  map.addSource("hover", { type: "geojson", data: EMPTY });
  map.addSource("stations", { type: "geojson", data: EMPTY });
  map.addSource("pireps", { type: "geojson", data: EMPTY });
  map.addSource("discussions", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "warnings-fill", type: "fill", source: "warnings",
    paint: {
      "fill-color": ["get", "color"],
      "fill-opacity": ["match", ["get", "significance"], "W", 0.32, "A", 0.18, 0.14],
    },
  });
  map.addLayer({
    id: "warnings-line", type: "line", source: "warnings",
    paint: { "line-color": ["get", "color"], "line-width": ["case", ["get", "storm_based"], 2.2, 1.1] },
  });
  // SPC mesoscale discussions and WPC precipitation discussions: dashed,
  // above the warnings they often precede.
  map.addLayer({
    id: "disc-fill", type: "fill", source: "discussions",
    paint: { "fill-color": ["get", "color"], "fill-opacity": 0.08 },
  });
  map.addLayer({
    id: "disc-line", type: "line", source: "discussions",
    paint: { "line-color": ["get", "color"], "line-width": 2, "line-dasharray": [3, 2] },
  });
  map.addLayer({
    id: "disc-label", type: "symbol", source: "discussions",
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 12 },
    paint: { "text-color": ["get", "color"], "text-halo-color": "#0b1016", "text-halo-width": 1.5 },
  });
  map.addLayer({
    id: "hover-line", type: "line", source: "hover",
    paint: { "line-color": "#ffffff", "line-width": 1.5, "line-opacity": 0.55 },
  });
  map.addLayer({
    id: "focus-line", type: "line", source: "focus",
    paint: { "line-color": "#ffffff", "line-width": 3, "line-dasharray": [2, 1] },
  });
  // The spot clicked for a zone forecast.
  map.addLayer({
    id: "focus-pt", type: "circle", source: "focus", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 5, "circle-color": "#ffffff", "circle-stroke-color": "#0b1016", "circle-stroke-width": 2 },
  });
  map.addLayer({
    id: "stations-dot", type: "circle", source: "stations",
    paint: {
      "circle-color": catColor,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.5, 7, 5, 10, 7],
      "circle-stroke-color": "#0b1016",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 8, 1.5],
    },
  });
  map.addLayer({
    id: "stations-label", type: "symbol", source: "stations", minzoom: 6.5,
    layout: {
      "text-field": ["concat", ["get", "station"],
        ["case", ["has", "temp_f"], ["concat", "  ", ["to-string", ["get", "temp_f"]], "°"], ""]],
      "text-font": ["Noto Sans Regular"],
      "text-size": 11,
      "text-offset": [0, 1.1],
      "text-anchor": "top",
    },
    paint: { "text-color": "#c9d4df", "text-halo-color": "#0b1016", "text-halo-width": 1.2 },
  });
  map.addLayer({
    id: "pireps-dot", type: "circle", source: "pireps",
    paint: {
      "circle-color": ["match", ["get", "hazard"], "TB", "#ff9f1a", "IC", "#5fd3ff", "#d0d6dc"],
      "circle-radius": ["case", ["get", "urgent"], 8, 6],
      "circle-opacity": 0.15,
      "circle-stroke-color": ["match", ["get", "hazard"], "TB", "#ff9f1a", "IC", "#5fd3ff", "#d0d6dc"],
      "circle-stroke-width": ["case", ["get", "urgent"], 3, 2],
    },
  });
}

export default function MapView() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [legend, setLegend] = useState<Legend>([]);
  const [counts, setCounts] = useState({ stations: 0, pireps: 0 });
  const [layers, setLayers] = useState<Layers>({ warnings: true, discussions: true, stations: true, pireps: true });
  const [nDisc, setNDisc] = useState(0);
  // Hover preview: the zone under a resting cursor. The tooltip follows the
  // mouse through its ref (no re-render per mousemove); only its text is state.
  const [hoverZone, setHoverZone] = useState<string | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [inspect, setInspect] = useState<Inspect | null>(null);
  // Phone only: the layer switches and legend fold behind a button.
  const [showLayers, setShowLayers] = useState(false);
  const [sp, setSp] = useSearchParams();

  const openRef = useRef<(i: Inspect | null) => void>(() => {});
  const open = useCallback((i: Inspect | null) => {
    setInspect(i);
    if (i) setShowLayers(false);
    // Keep the URL shareable: ?product=… / ?station=… / ?at=lon,lat reopen the panel.
    const p = new URLSearchParams();
    if (i?.kind === "product") p.set("product", String(i.id));
    if (i?.kind === "station") p.set("station", i.id);
    if (i?.kind === "zone") p.set("at", `${i.lon.toFixed(4)},${i.lat.toFixed(4)}`);
    setSp(p, { replace: true });
  }, [setSp]);
  openRef.current = open;

  const debounced = (fn: () => void) => {
    let t: number | undefined;
    return () => {
      clearTimeout(t);
      t = window.setTimeout(fn, 500);
    };
  };

  const refreshWarnings = useMemo(() => debounced(() => {
    api.warnings().then((fc) => {
      (mapRef.current?.getSource("warnings") as GeoJSONSource | undefined)?.setData(fc);
      const m = new Map<string, Legend[number]>();
      for (const f of fc.features) {
        const p = f.properties as { name: string; color: string };
        const e = m.get(p.name) ?? { ...p, n: 0 };
        e.n++;
        m.set(p.name, e);
      }
      setLegend([...m.values()].sort((a, b) => b.n - a.n));
    });
  }), []);

  const refreshDiscussions = useMemo(() => debounced(() => {
    api.discussions().then((fc) => {
      (mapRef.current?.getSource("discussions") as GeoJSONSource | undefined)?.setData(fc);
      setNDisc(fc.features.length);
    });
  }), []);

  const refreshObs = useMemo(() => debounced(() => {
    const map = mapRef.current;
    if (!map) return;
    const b = map.getBounds();
    // Longitudes are wrapped into [-180, 180); a view that crosses 180°
    // then has west > east, which the API treats as an antimeridian box.
    // Zoomed out past one full world, ask for everything.
    const wrap = (v: number) => ((((v + 180) % 360) + 360) % 360) - 180;
    const lat = (v: number) => Math.max(-90, Math.min(90, v));
    const bbox = b.getEast() - b.getWest() >= 360
      ? "-180,-90,180,90"
      : [wrap(b.getWest()), lat(b.getSouth()), wrap(b.getEast()), lat(b.getNorth())].map((v) => v.toFixed(3)).join(",");
    api.stations({ bbox, zoom: Math.floor(map.getZoom()) }).then((fc) => {
      for (const f of fc.features) {
        const p = f.properties as { temp_c?: number; temp_f?: number };
        if (p.temp_c != null) p.temp_f = Math.round((p.temp_c * 9) / 5 + 32);
      }
      (map.getSource("stations") as GeoJSONSource | undefined)?.setData(fc);
      setCounts((c) => ({ ...c, stations: fc.features.length }));
    });
    api.pireps().then((fc) => {
      (map.getSource("pireps") as GeoJSONSource | undefined)?.setData(fc);
      setCounts((c) => ({ ...c, pireps: fc.features.length }));
    });
  }), []);

  const refreshObsRef = useRef(refreshObs);
  refreshObsRef.current = refreshObs;

  useEffect(() => {
    let cancelled = false;
    basemapStyle()
      .then((style) => {
        if (cancelled || !el.current) return;
        const map = new maplibregl.Map({ container: el.current, style, ...START, ...FLAT,
          attributionControl: { compact: true } });
        mapRef.current = map;
        map.addControl(new maplibregl.NavigationControl(), "top-right");
        map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
        map.on("load", () => {
          addLayers(map);
          setReady(true);
        });
        let hoverTimer: number | undefined;
        let hoverSeq = 0;
        let hoverGeom: GeoJSON.Geometry | null = null;
        const hover = canHover();
        const hideHover = () => {
          clearTimeout(hoverTimer);
          hoverSeq++;
          hoverGeom = null;
          (map.getSource("hover") as GeoJSONSource | undefined)?.setData(EMPTY);
          setHoverZone(null);
        };
        map.on("mousemove", (e) => {
          if (!hover) return;
          if (tipRef.current) {
            tipRef.current.style.left = `${e.point.x + 14}px`;
            tipRef.current.style.top = `${e.point.y + 14}px`;
          }
          const layers = FEATURE_LAYERS.filter((l) => map.getLayer(l));
          if (map.queryRenderedFeatures(e.point, { layers }).length) return hideHover();
          const ll: [number, number] = [e.lngLat.lng, e.lngLat.lat];
          if (hoverGeom && inside(ll, hoverGeom)) return;
          clearTimeout(hoverTimer);
          hoverTimer = window.setTimeout(() => {
            const n = ++hoverSeq;
            api.zoneBrief(ll[0], ll[1]).then((z) => {
              if (n !== hoverSeq) return;
              hoverGeom = z.zone?.geometry ?? null;
              (map.getSource("hover") as GeoJSONSource | undefined)?.setData(
                hoverGeom ? { type: "Feature", geometry: hoverGeom, properties: {} } : EMPTY);
              setHoverZone(z.zone?.name ?? null);
              map.getCanvas().style.cursor = z.zone ? "pointer" : "";
            }).catch(() => {});
          }, 250);
        });
        map.on("mouseout", hideHover);
        map.on("dragstart", hideHover);
        // Stations are fetched for the view: the set changes with zoom.
        map.on("moveend", () => refreshObsRef.current());
        // One click handler, most specific layer first: a station or a
        // PIREP sits on top of the warning polygon it is inside. Anywhere
        // else opens the zone forecast for that spot.
        const pick = (e: maplibregl.MapMouseEvent) => {
          const hit = (ids: string[]) =>
            map.queryRenderedFeatures(e.point, { layers: ids.filter((l) => map.getLayer(l)) });
          const st = hit(["stations-dot"]);
          if (st.length) return openRef.current({ kind: "station", id: st[0].properties.station });
          const pr = hit(["pireps-dot"]);
          if (pr.length) {
            const p = pr[0].properties;
            return openRef.current({ kind: "pirep", raw: p.raw, time: p.observed,
              // maplibre 6 keeps nested GeoJSON properties as objects (5 gave a JSON string).
              decoded: { type: "PIREP", station: p.station,
                         decoded: typeof p.decoded === "string" ? JSON.parse(p.decoded) : p.decoded } as PirepDecoded });
          }
          const at: [number, number] = [e.lngLat.lng, e.lngLat.lat];
          const d = hit(["disc-fill"]);
          if (d.length) return openRef.current({ kind: "product", id: d[0].properties.product_id as number, at });
          const w = hit(["warnings-fill"]);
          if (!w.length) return openRef.current({ kind: "zone", lon: at[0], lat: at[1] });
          const seen = new Map<number, MapGeoJSONFeature>();
          w.forEach((f) => seen.set(f.properties.product_id, f));
          const items = [...seen.values()].map((f) => ({
            id: f.properties.product_id as number, name: f.properties.name as string,
            color: f.properties.color as string, office: f.properties.office as string,
          }));
          openRef.current(items.length === 1 ? { kind: "product", id: items[0].id, at } : { kind: "products", items, at });
        };
        map.on("click", (e) => {
          pick(e);
          clearOfSheet(map, e.point);
        });
        for (const l of ["stations-dot", "pireps-dot", "disc-fill", "warnings-fill"]) {
          map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
          map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
        }
      })
      .catch((e) => setErr(String(e)));
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
    // Created once. Anything it calls back into goes through refs.
  }, []);

  // Load, then keep current: on every relevant product, and every minute
  // so expired warnings and stale observations drop off.
  useEffect(() => {
    if (!ready) return;
    refreshWarnings();
    refreshDiscussions();
    refreshObs();
    const t = setInterval(() => {
      refreshWarnings();
      refreshDiscussions();
      refreshObs();
    }, 60_000);
    return () => clearInterval(t);
  }, [ready, refreshWarnings, refreshDiscussions, refreshObs]);
  useLiveEvents((e) => {
    if (!ready || e.type !== "product") return;
    if (e.vtec) refreshWarnings();
    if (e.nnn === "SWO" || e.nnn === "FFG") refreshDiscussions();
    if (e.category === "observation" || e.nnn === "TAF" || e.nnn === "UAB") refreshObs();
  });

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    for (const [k, ids] of Object.entries(LAYER_IDS))
      for (const id of ids) map.setLayoutProperty(id, "visibility", layers[k as keyof Layers] ? "visible" : "none");
  }, [ready, layers]);

  // Outline a product's polygon (and fly to it) when it is the one open.
  const showProduct = useCallback((id: number, fly: boolean) => {
    const map = mapRef.current;
    if (!map) return;
    const src = map.getSource("focus") as GeoJSONSource;
    api.product(id).then((p) => {
      const polys = p.segments.filter((s) => s.polygon).map((s) => s.polygon!);
      src.setData({ type: "FeatureCollection",
        features: polys.map((g) => ({ type: "Feature", geometry: g, properties: {} })) });
      if (fly && polys.length) {
        const b = new maplibregl.LngLatBounds();
        polys.forEach((g) => g.coordinates[0].forEach((c) => b.extend(c as [number, number])));
        map.fitBounds(b, { padding: fitPadding(map), maxZoom: 9 });
      }
    });
  }, []);

  useEffect(() => {
    if (!ready) return;
    const src = mapRef.current?.getSource("focus") as GeoJSONSource | undefined;
    if (inspect?.kind === "product") showProduct(inspect.id, false);
    else if (inspect?.kind === "zone") showZone(null);
    else src?.setData(EMPTY);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, inspect, showProduct]);

  // The zone panel hands back its zone's outline once loaded; the clicked
  // point is drawn at once. A deep link (?at=) also brings the zone into
  // view; a click leaves the map where it is.
  const flyToZone = useRef(false);
  const showZone = useCallback((g: GeoJSON.Geometry | null) => {
    const map = mapRef.current;
    const src = map?.getSource("focus") as GeoJSONSource | undefined;
    if (!map || !src || inspect?.kind !== "zone") return;
    if (g && flyToZone.current) {
      flyToZone.current = false;
      const b = new maplibregl.LngLatBounds();
      const walk = (c: unknown): void => {
        if (Array.isArray(c) && typeof c[0] === "number") b.extend(c as [number, number]);
        else if (Array.isArray(c)) c.forEach(walk);
      };
      walk((g as GeoJSON.Polygon | GeoJSON.MultiPolygon).coordinates);
      map.fitBounds(b, { padding: fitPadding(map), maxZoom: 9 });
    }
    const pt: GeoJSON.Feature = { type: "Feature", geometry: { type: "Point", coordinates: [inspect.lon, inspect.lat] },
      properties: {} };
    src.setData({ type: "FeatureCollection",
      features: g ? [{ type: "Feature", geometry: g, properties: {} }, pt] : [pt] });
  }, [inspect]);

  // Deep links: /map?product=<id> or /map?station=<id> open the panel.
  useEffect(() => {
    if (!ready) return;
    const pid = sp.get("product");
    const sid = sp.get("station");
    const at = sp.get("at")?.split(",").map(Number);
    if (pid && !(inspect?.kind === "product" && inspect.id === Number(pid))) {
      setInspect({ kind: "product", id: Number(pid) });
      showProduct(Number(pid), true);
    } else if (sid && !(inspect?.kind === "station" && inspect.id === sid)) {
      setInspect({ kind: "station", id: sid });
    } else if (at?.length === 2 && at.every(Number.isFinite)) {
      flyToZone.current = true;
      setInspect({ kind: "zone", lon: at[0], lat: at[1] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  return (
    <div className="mapwrap">
      <div ref={el} className="map" />
      {err && <div className="maperr">Basemap unavailable: {err}</div>}
      <div ref={tipRef} className="maptip" style={{ display: hoverZone ? "block" : "none" }}>
        <b>{hoverZone}</b> · click for forecast
      </div>
      <div className="mapside">
        <div className="maptop">
          <PlaceSearch
            near={() => { const c = mapRef.current?.getCenter(); return c ? [c.lng, c.lat] : undefined; }}
            onPick={(lon, lat) => { flyToZone.current = true; open({ kind: "zone", lon, lat }); }}
          />
          <button className={`layerbtn${showLayers ? " on" : ""}`} onClick={() => setShowLayers((v) => !v)}
            aria-expanded={showLayers} title="Layers and legend">☰</button>
        </div>
        <div className={`mapopts${showLayers ? " open" : ""}`}>
          <div className="layers">
            {(["warnings", "discussions", "stations", "pireps"] as const).map((k) => (
              <label key={k}>
                <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
                {k === "warnings" ? `Warnings (${legend.reduce((a, l) => a + l.n, 0)})`
                  : k === "discussions" ? `Mesoscale discussions (${nDisc})`
                  : k === "stations" ? `METAR stations (${counts.stations} in view)` : `PIREPs (${counts.pireps})`}
              </label>
            ))}
            {layers.stations && (
              <div className="catkey">
                {(["VFR", "MVFR", "IFR", "LIFR"] as const).map((c) => (
                  <span key={c}><span className="sw" style={{ background: CAT_COLOR[c] }} />{c}</span>
                ))}
              </div>
            )}
          </div>
          {layers.warnings && legend.length > 0 && (
            <div className="legend">
              {legend.map((l) => (
                <div key={l.name} className="legend-row">
                  <span className="sw" style={{ background: l.color }} />
                  {l.name}
                  <span className="n">{l.n}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {inspect && (
        <Inspector item={inspect} onClose={() => open(null)} onOpen={open} onZone={showZone}
          onShowProduct={(id) => showProduct(id, true)} />
      )}
    </div>
  );
}
