import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import maplibregl, { type GeoJSONSource, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle } from "../map/basemap";
import { api, type PirepDecoded } from "../api";
import { useLiveEvents } from "../events";
import Inspector, { type Inspect } from "../map/Inspector";
import { CAT_COLOR } from "../wx/Wx";

// GOES-18 sits at 137.0°W: open over the western US and the eastern
// Pacific, the part of its disk this site is about.
const START = { center: [-118, 38] as [number, number], zoom: 3.6 };
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Legend = { name: string; color: string; n: number }[];
type Layers = { warnings: boolean; stations: boolean; pireps: boolean };
const LAYER_IDS: Record<keyof Layers, string[]> = {
  warnings: ["warnings-fill", "warnings-line"],
  stations: ["stations-dot", "stations-label"],
  pireps: ["pireps-dot"],
};

const catColor = ["match", ["get", "flight_category"],
  "VFR", CAT_COLOR.VFR, "MVFR", CAT_COLOR.MVFR, "IFR", CAT_COLOR.IFR, "LIFR", CAT_COLOR.LIFR, CAT_COLOR.UNK,
] as unknown as maplibregl.ExpressionSpecification;

function addLayers(map: maplibregl.Map) {
  map.addSource("warnings", { type: "geojson", data: EMPTY });
  map.addSource("focus", { type: "geojson", data: EMPTY });
  map.addSource("stations", { type: "geojson", data: EMPTY });
  map.addSource("pireps", { type: "geojson", data: EMPTY });
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
  map.addLayer({
    id: "focus-line", type: "line", source: "focus",
    paint: { "line-color": "#ffffff", "line-width": 3, "line-dasharray": [2, 1] },
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
  const [layers, setLayers] = useState<Layers>({ warnings: true, stations: true, pireps: true });
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [sp, setSp] = useSearchParams();

  const open = useCallback((i: Inspect | null) => {
    setInspect(i);
    // Keep the URL shareable: ?product=… / ?station=… reopen the panel.
    const p = new URLSearchParams();
    if (i?.kind === "product") p.set("product", String(i.id));
    if (i?.kind === "station") p.set("station", i.id);
    setSp(p, { replace: true });
  }, [setSp]);

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

  const refreshObs = useMemo(() => debounced(() => {
    api.stations().then((fc) => {
      for (const f of fc.features) {
        const p = f.properties as { temp_c: number | null; temp_f?: number };
        if (p.temp_c != null) p.temp_f = Math.round((p.temp_c * 9) / 5 + 32);
      }
      (mapRef.current?.getSource("stations") as GeoJSONSource | undefined)?.setData(fc);
      setCounts((c) => ({ ...c, stations: fc.features.length }));
    });
    api.pireps().then((fc) => {
      (mapRef.current?.getSource("pireps") as GeoJSONSource | undefined)?.setData(fc);
      setCounts((c) => ({ ...c, pireps: fc.features.length }));
    });
  }), []);

  useEffect(() => {
    let cancelled = false;
    basemapStyle()
      .then((style) => {
        if (cancelled || !el.current) return;
        const map = new maplibregl.Map({ container: el.current, style, ...START, attributionControl: { compact: true } });
        mapRef.current = map;
        map.addControl(new maplibregl.NavigationControl(), "top-right");
        map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
        map.on("load", () => {
          addLayers(map);
          setReady(true);
        });
        // One click handler, most specific layer first: a station or a
        // PIREP sits on top of the warning polygon it is inside.
        map.on("click", (e) => {
          const hit = (ids: string[]) =>
            map.queryRenderedFeatures(e.point, { layers: ids.filter((l) => map.getLayer(l)) });
          const st = hit(["stations-dot"]);
          if (st.length) return open({ kind: "station", id: st[0].properties.station });
          const pr = hit(["pireps-dot"]);
          if (pr.length) {
            const p = pr[0].properties;
            return open({ kind: "pirep", raw: p.raw, time: p.observed,
              decoded: { type: "PIREP", station: p.station, decoded: JSON.parse(p.decoded) } as PirepDecoded });
          }
          const w = hit(["warnings-fill"]);
          if (!w.length) return;
          const seen = new Map<number, MapGeoJSONFeature>();
          w.forEach((f) => seen.set(f.properties.product_id, f));
          const items = [...seen.values()].map((f) => ({
            id: f.properties.product_id as number, name: f.properties.name as string,
            color: f.properties.color as string, office: f.properties.office as string,
          }));
          open(items.length === 1 ? { kind: "product", id: items[0].id } : { kind: "products", items });
        });
        for (const l of ["stations-dot", "pireps-dot", "warnings-fill"]) {
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
  }, [open]);

  // Load, then keep current: on every relevant product, and every minute
  // so expired warnings and stale observations drop off.
  useEffect(() => {
    if (!ready) return;
    refreshWarnings();
    refreshObs();
    const t = setInterval(() => {
      refreshWarnings();
      refreshObs();
    }, 60_000);
    return () => clearInterval(t);
  }, [ready, refreshWarnings, refreshObs]);
  useLiveEvents((e) => {
    if (!ready || e.type !== "product") return;
    if (e.vtec) refreshWarnings();
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
        map.fitBounds(b, { padding: { top: 80, bottom: 80, left: 80, right: 480 }, maxZoom: 9 });
      }
    });
  }, []);

  useEffect(() => {
    if (!ready) return;
    const src = mapRef.current?.getSource("focus") as GeoJSONSource | undefined;
    if (inspect?.kind === "product") showProduct(inspect.id, false);
    else src?.setData(EMPTY);
  }, [ready, inspect, showProduct]);

  // Deep links: /map?product=<id> or /map?station=<id> open the panel.
  useEffect(() => {
    if (!ready) return;
    const pid = sp.get("product");
    const sid = sp.get("station");
    if (pid && !(inspect?.kind === "product" && inspect.id === Number(pid))) {
      setInspect({ kind: "product", id: Number(pid) });
      showProduct(Number(pid), true);
    } else if (sid && !(inspect?.kind === "station" && inspect.id === sid)) {
      setInspect({ kind: "station", id: sid });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  return (
    <div className="mapwrap">
      <div ref={el} className="map" />
      {err && <div className="maperr">Basemap unavailable: {err}</div>}
      <div className="mapside">
        <div className="layers">
          {(["warnings", "stations", "pireps"] as const).map((k) => (
            <label key={k}>
              <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
              {k === "warnings" ? `Warnings (${legend.reduce((a, l) => a + l.n, 0)})`
                : k === "stations" ? `METAR stations (${counts.stations})` : `PIREPs (${counts.pireps})`}
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
      {inspect && (
        <Inspector item={inspect} onClose={() => open(null)} onOpen={open}
          onShowProduct={(id) => showProduct(id, true)} />
      )}
    </div>
  );
}
