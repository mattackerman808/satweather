import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import maplibregl, { type GeoJSONSource, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle } from "../map/basemap";
import { api, fmtTime } from "../api";
import { useLiveEvents } from "../events";

// GOES-18 sits at 137.0°W: open over the western US and the eastern
// Pacific, the part of its disk this site is about.
const START = { center: [-118, 38] as [number, number], zoom: 3.6 };
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Legend = { name: string; color: string; n: number }[];

function addWarningLayers(map: maplibregl.Map) {
  map.addSource("warnings", { type: "geojson", data: EMPTY });
  map.addSource("focus", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "warnings-fill",
    type: "fill",
    source: "warnings",
    paint: {
      "fill-color": ["get", "color"],
      "fill-opacity": ["match", ["get", "significance"], "W", 0.32, "A", 0.18, 0.14],
    },
  });
  map.addLayer({
    id: "warnings-line",
    type: "line",
    source: "warnings",
    paint: {
      "line-color": ["get", "color"],
      "line-width": ["case", ["get", "storm_based"], 2.2, 1.1],
    },
  });
  map.addLayer({
    id: "focus-line",
    type: "line",
    source: "focus",
    paint: { "line-color": "#ffffff", "line-width": 3, "line-dasharray": [2, 1] },
  });
}

function popupHtml(fs: MapGeoJSONFeature[]) {
  const seen = new Set<string>();
  return fs
    .filter((f) => !seen.has(f.properties.key) && seen.add(f.properties.key))
    .map((f) => {
      const p = f.properties;
      return `<div class="wpop"><span class="sw" style="background:${p.color}"></span>
        <b>${p.name}</b><div class="dim">${p.office} #${p.etn} · until ${fmtTime(p.ends)}</div>
        <a href="/text/${p.product_id}" data-pid="${p.product_id}">${escape(p.title)}</a></div>`;
    })
    .join("");
}

function escape(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

export default function MapView() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [legend, setLegend] = useState<Legend>([]);
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const focusId = sp.get("product");

  const refresh = useMemo(() => {
    let t: number | undefined;
    return () => {
      clearTimeout(t);
      t = window.setTimeout(() => {
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
      }, 500);
    };
  }, []);

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
          addWarningLayers(map);
          setReady(true);
        });
        map.on("click", "warnings-fill", (e) => {
          const pop = new maplibregl.Popup({ maxWidth: "320px" })
            .setLngLat(e.lngLat)
            .setHTML(popupHtml(e.features ?? []))
            .addTo(map);
          pop.getElement().addEventListener("click", (ev) => {
            const a = (ev.target as HTMLElement).closest("a[data-pid]") as HTMLAnchorElement | null;
            if (a) {
              ev.preventDefault();
              nav(`/text/${a.dataset.pid}`);
            }
          });
        });
        map.on("mouseenter", "warnings-fill", () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", "warnings-fill", () => (map.getCanvas().style.cursor = ""));
      })
      .catch((e) => setErr(String(e)));
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, [nav]);

  // Load, then keep current: on every VTEC product, and once a minute so
  // expired events drop off.
  useEffect(() => {
    if (!ready) return;
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  }, [ready, refresh]);
  useLiveEvents((e) => {
    if (ready && e.type === "product" && e.vtec) refresh();
  });

  // /map?product=<id>: outline that product's polygon and fly to it.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const src = map.getSource("focus") as GeoJSONSource;
    if (!focusId) {
      src.setData(EMPTY);
      return;
    }
    api.product(Number(focusId)).then((p) => {
      const polys = p.segments.filter((s) => s.polygon).map((s) => s.polygon!);
      if (!polys.length) return;
      src.setData({ type: "FeatureCollection", features: polys.map((g) => ({ type: "Feature", geometry: g, properties: {} })) });
      const b = new maplibregl.LngLatBounds();
      polys.forEach((g) => g.coordinates[0].forEach((c) => b.extend(c as [number, number])));
      map.fitBounds(b, { padding: 80, maxZoom: 9 });
    });
  }, [ready, focusId]);

  return (
    <div className="mapwrap">
      <div ref={el} className="map" />
      {err && <div className="maperr">Basemap unavailable: {err}</div>}
      {legend.length > 0 && (
        <div className="legend">
          <div className="legend-title">Active ({legend.reduce((a, l) => a + l.n, 0)})</div>
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
  );
}
