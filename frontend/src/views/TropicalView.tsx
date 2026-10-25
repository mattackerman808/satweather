import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ago, api, fmtTime, type Frame, type Outlook, type Sector, type Storm, type TcPoint } from "../api";
import { useLiveEvents } from "../events";
import { basemapStyle } from "../map/basemap";
import Panel from "../map/Panel";
import { clearOfSheet, fitPadding } from "../narrow";

// Tropical cyclones and the mesoscale picture, all from the dish: NHC/CPHC
// forecast/advisories (track, forecast points, wind radii, exactly as
// written; no cone, which is not in the text), outlooks, SPC/WPC
// discussions, and where GOES-18's meso sectors are pointed.

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
// Saffir-Simpson colours, the conventional scale.
const CAT_COLOR: Record<string, string> = {
  TD: "#5ebaff", TS: "#00faf4", "1": "#ffffcc", "2": "#ffe775", "3": "#ffc140", "4": "#ff8f20", "5": "#ff6060",
  "?": "#c0c0c0",
};
const RADII_COLOR: Record<string, string> = { "34": "#f5d442", "50": "#ff9f1a", "64": "#ff4d4d" };
const catColor = ["match", ["get", "category"], ...Object.entries(CAT_COLOR).flat(), "#c0c0c0"] as unknown as
  maplibregl.ExpressionSpecification;
const IR = "Clean_Longwave_IR_Window_Band";

const mph = (kt: number | null | undefined) => (kt == null ? "" : `${Math.round(kt * 1.15078)} mph`);
const catLabel = (c: string) => (c === "TD" || c === "TS" || c === "?" ? c : `Cat ${c}`);
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg: number) => COMPASS[Math.round(deg / 22.5) % 16];
const ll = (lat: number | null, lon: number | null) =>
  lat == null || lon == null ? "—" : `${Math.abs(lat).toFixed(1)}°${lat < 0 ? "S" : "N"} ${Math.abs(lon).toFixed(1)}°${lon < 0 ? "W" : "E"}`;
const zday = (iso: string) => {
  const d = new Date(iso);
  return `${d.toLocaleDateString([], { weekday: "short", timeZone: "UTC" })} ${d.toISOString().slice(11, 13)}Z`;
};

function features(storms: Storm[], sectors: Sector[]) {
  const radii: GeoJSON.Feature[] = [];
  const lines: GeoJSON.Feature[] = [];
  const points: GeoJSON.Feature[] = [];
  for (const s of storms) {
    // Largest radius first, so the smaller ones draw on top.
    for (const a of [...s.radii_areas].sort((x, y) => Number(x.kt) - Number(y.kt)))
      radii.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [a.ring] },
        properties: { kt: a.kt, color: RADII_COLOR[a.kt], storm: s.storm_id } });
    const past = s.track.filter((p) => p.lat != null).map((p) => [p.lon!, p.lat!]);
    if (past.length > 1) lines.push({ type: "Feature", geometry: { type: "LineString", coordinates: past },
      properties: { kind: "past", storm: s.storm_id } });
    const fc = s.forecast.filter((p) => p.lat != null);
    lines.push({ type: "Feature", geometry: { type: "LineString",
      coordinates: [[s.lon, s.lat], ...fc.map((p) => [p.lon!, p.lat!])] }, properties: { kind: "forecast", storm: s.storm_id } });
    for (const p of s.track.slice(0, -1))
      if (p.lat != null) points.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon!, p.lat] },
        properties: { kind: "past", category: p.category ?? "?", storm: s.storm_id } });
    for (const p of fc)
      points.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon!, p.lat!] },
        properties: { kind: "forecast", category: p.category ?? "?", storm: s.storm_id,
          label: `${zday(p.valid)} ${p.wind_kt ?? "?"} kt` } });
    points.push({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      properties: { kind: "now", category: s.category, storm: s.storm_id, name: s.name } });
  }
  const secs: GeoJSON.Feature[] = sectors.flatMap((x) => [
    { type: "Feature", geometry: { type: "Polygon", coordinates: [x.ring] }, properties: { sector: x.sector } },
    { type: "Feature", geometry: { type: "Point", coordinates: [x.ring[0][0], x.ring[0][1]] },
      properties: { label: x.sector.replace("Mesoscale", "Meso") } },
  ] as GeoJSON.Feature[]);
  const fcol = (f: GeoJSON.Feature[]): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features: f });
  return { radii: fcol(radii), lines: fcol(lines), points: fcol(points), sectors: fcol(secs) };
}

function addLayers(map: maplibregl.Map) {
  for (const id of ["sectors", "discussions", "radii", "lines", "points"]) map.addSource(id, { type: "geojson", data: EMPTY });
  map.addLayer({ id: "sec-line", type: "line", source: "sectors", filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "line-color": "#4fb3ff", "line-width": 1.5, "line-dasharray": [4, 3], "line-opacity": 0.8 } });
  map.addLayer({ id: "sec-label", type: "symbol", source: "sectors", filter: ["==", ["geometry-type"], "Point"],
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 11, "text-anchor": "bottom-left",
      "text-offset": [0.3, -0.2] },
    paint: { "text-color": "#4fb3ff", "text-halo-color": "#0b1016", "text-halo-width": 1.5 } });
  map.addLayer({ id: "disc-fill", type: "fill", source: "discussions", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.08 } });
  map.addLayer({ id: "disc-line", type: "line", source: "discussions",
    paint: { "line-color": ["get", "color"], "line-width": 2, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "disc-label", type: "symbol", source: "discussions",
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 12 },
    paint: { "text-color": ["get", "color"], "text-halo-color": "#0b1016", "text-halo-width": 1.5 } });
  map.addLayer({ id: "radii-fill", type: "fill", source: "radii", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.2 } });
  map.addLayer({ id: "radii-line", type: "line", source: "radii", paint: { "line-color": ["get", "color"], "line-width": 1, "line-opacity": 0.7 } });
  map.addLayer({ id: "track-past", type: "line", source: "lines", filter: ["==", ["get", "kind"], "past"],
    paint: { "line-color": "#ffffff", "line-width": 2, "line-opacity": 0.85 } });
  map.addLayer({ id: "track-fcst", type: "line", source: "lines", filter: ["==", ["get", "kind"], "forecast"],
    paint: { "line-color": "#ffffff", "line-width": 1.5, "line-dasharray": [2, 2], "line-opacity": 0.85 } });
  map.addLayer({ id: "pts", type: "circle", source: "points", filter: ["!=", ["get", "kind"], "now"],
    paint: { "circle-color": catColor, "circle-radius": ["case", ["==", ["get", "kind"], "past"], 3.5, 5],
      "circle-stroke-color": "#0b1016", "circle-stroke-width": 1.2 } });
  map.addLayer({ id: "pts-label", type: "symbol", source: "points", filter: ["==", ["get", "kind"], "forecast"], minzoom: 4.5,
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-anchor": "left",
      "text-offset": [0.8, 0] },
    paint: { "text-color": "#d6dee7", "text-halo-color": "#0b1016", "text-halo-width": 1.3 } });
  map.addLayer({ id: "now", type: "circle", source: "points", filter: ["==", ["get", "kind"], "now"],
    paint: { "circle-color": catColor, "circle-radius": 11, "circle-stroke-color": "#0b1016", "circle-stroke-width": 2 } });
  map.addLayer({ id: "now-label", type: "symbol", source: "points", filter: ["==", ["get", "kind"], "now"],
    layout: { "text-field": ["get", "category"], "text-font": ["Noto Sans Bold"], "text-size": 11,
      "text-allow-overlap": true },
    paint: { "text-color": "#0b1016", "text-halo-color": "rgba(0,0,0,0)" } });
  map.addLayer({ id: "now-name", type: "symbol", source: "points", filter: ["==", ["get", "kind"], "now"],
    layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"], "text-size": 13, "text-anchor": "top",
      "text-offset": [0, 1.2], "text-allow-overlap": true },
    paint: { "text-color": "#ffffff", "text-halo-color": "#0b1016", "text-halo-width": 1.6 } });
}

export default function TropicalView() {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [storms, setStorms] = useState<Storm[] | null>(null);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [outlooks, setOutlooks] = useState<Outlook[]>([]);
  const [discs, setDiscs] = useState<GeoJSON.Feature[]>([]);
  const [sp, setSp] = useSearchParams();
  const selected = storms?.find((s) => s.storm_id === sp.get("storm")) ?? null;
  // Phone only: the storm list and outlooks fold into a bar over the map.
  const [listOpen, setListOpen] = useState(false);
  const selectRef = useRef<(id: string | null, fly?: boolean) => void>(() => {});
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const load = useCallback(() => {
    api.tropical().then((d) => { setStorms(d.storms); setSectors(d.sectors); }).catch((e) => setErr(String(e)));
    api.outlooks().then(setOutlooks).catch(() => {});
    api.discussions().then((fc) => setDiscs(fc.features)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!el.current) return;
    let cancelled = false;
    basemapStyle().then((style) => {
      if (cancelled || !el.current) return;
      const map = new maplibregl.Map({ container: el.current, style, center: [-120, 18], zoom: 2.8,
        attributionControl: { compact: true } });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl(), "top-right");
      map.on("load", () => { addLayers(map); setReady(true); });
      map.on("click", (e) => {
        const hit = map.queryRenderedFeatures(e.point, { layers: ["now", "pts", "radii-fill"] })[0];
        if (hit) {
          selectRef.current(hit.properties.storm as string, false);
          return clearOfSheet(map, e.point);
        }
        const d = map.queryRenderedFeatures(e.point, { layers: ["disc-fill"] })[0];
        if (d) navigateRef.current(`/map?product=${d.properties.product_id}`);
      });
      for (const l of ["now", "pts", "radii-fill", "disc-fill"]) {
        map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
      }
    }).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, 120_000);
    return () => { cancelled = true; clearInterval(t); mapRef.current?.remove(); mapRef.current = null; };
  }, [load]);

  useLiveEvents((e) => {
    if (e.type === "product" && ["TCM", "TCP", "TWO", "SWO", "FFG"].includes(e.nnn)) load();
  });

  const geo = useMemo(() => features(storms ?? [], sectors), [storms, sectors]);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource("radii") as GeoJSONSource).setData(geo.radii);
    (map.getSource("lines") as GeoJSONSource).setData(geo.lines);
    (map.getSource("points") as GeoJSONSource).setData(geo.points);
    (map.getSource("sectors") as GeoJSONSource).setData(geo.sectors);
    (map.getSource("discussions") as GeoJSONSource).setData({ type: "FeatureCollection", features: discs });
  }, [ready, geo, discs]);

  const select = useCallback((id: string | null, fly = true) => {
    const next = new URLSearchParams(sp);
    if (id) next.set("storm", id); else next.delete("storm");
    if (id) setListOpen(false);
    setSp(next, { replace: true });
    const s = storms?.find((x) => x.storm_id === id);
    const map = mapRef.current;
    if (s && map && fly) {
      const b = new maplibregl.LngLatBounds([s.lon, s.lat], [s.lon, s.lat]);
      for (const p of [...s.track, ...s.forecast]) if (p.lat != null) b.extend([p.lon!, p.lat]);
      map.fitBounds(b, { padding: fitPadding(map, 380), maxZoom: 6 });
    }
  }, [sp, setSp, storms]);
  selectRef.current = select;

  // A deep link (?storm=) zooms to its storm once the data is in.
  const flown = useRef(false);
  useEffect(() => {
    if (!flown.current && ready && selected) { flown.current = true; select(selected.storm_id); }
  }, [ready, selected, select]);

  return (
    <div className="mapwrap">
      <div ref={el} className="map" />
      {err && <div className="maperr">{err}</div>}
      <aside className={`tropside${listOpen ? " open" : ""}`}>
        <button className="tropbar" onClick={() => setListOpen((o) => !o)} aria-expanded={listOpen}>
          <span>Tropical cyclones <b>{storms?.length ?? "…"}</b> · outlooks · mesoscale</span>
          <span aria-hidden>{listOpen ? "▴" : "▾"}</span>
        </button>
        <section className="layers">
          <h3>Tropical cyclones</h3>
          {storms === null && <p className="dim">Loading…</p>}
          {storms?.length === 0 && <p className="dim">No NHC/CPHC advisories in the last 18 hours.</p>}
          {storms?.map((s) => (
            <button key={s.storm_id} className={`stormcard${selected?.storm_id === s.storm_id ? " on" : ""}`}
              onClick={() => select(s.storm_id)}>
              <span className="catbadge2" style={{ background: CAT_COLOR[s.category] }}>{s.category}</span>
              <span className="sc-main">
                <b>{s.kind} {s.name}</b>
                <span className="dim">{s.wind_kt} kt ({mph(s.wind_kt)}) · {s.pressure_mb} mb
                  {s.move_dir != null && ` · ${compass(s.move_dir)} ${s.move_kt} kt`}</span>
                <span className="dim">#{s.number} · {ago(s.issued)}{s.final ? " · final advisory" : ""}
                  {s.meso.length > 0 && ` · ${s.meso.map((m) => m.replace("Mesoscale", "Meso")).join(", ")} on it`}</span>
              </span>
            </button>
          ))}
        </section>
        <section className="layers">
          <h3>Outlooks <span className="dim">next 7 days</span></h3>
          {outlooks.map((o) => (
            <div key={o.awips} className="outlook">
              <Link to={`/text/${o.product_id}`}><b>{o.basin}</b></Link> <span className="dim">{ago(o.received)}</span>
              {o.none_expected && <div className="dim">No formation expected.</div>}
              {o.areas.map((a) => (
                <div key={a.title} className="olarea" title={a.text}>
                  {a.title}: <b>{a.chance_48h ?? "?"}%</b> 48 h, <b>{a.chance_7d ?? "?"}%</b> 7 days
                </div>
              ))}
            </div>
          ))}
        </section>
        <section className="layers">
          <h3>Mesoscale</h3>
          {discs.length === 0 && <p className="dim">No SPC or WPC discussions in effect.</p>}
          {discs.map((d) => (
            <Link key={d.properties!.product_id} className="olarea" to={`/map?product=${d.properties!.product_id}`}>
              <b style={{ color: d.properties!.color }}>{d.properties!.label}</b> {d.properties!.concerning}
            </Link>
          ))}
          {sectors.map((x) => (
            <Link key={x.sector} className="olarea"
              to={`/satellite?${new URLSearchParams({ src: x.source, sector: x.sector, product: IR })}`}>
              <b>{x.sector.replace("Mesoscale", "Meso")}</b> <span className="dim">
                centered {ll(x.center[1], x.center[0])} · loop →</span>
            </Link>
          ))}
        </section>
      </aside>
      {selected && <StormPanel s={selected} onClose={() => select(null, false)} />}
    </div>
  );
}

function StormPanel({ s, onClose }: { s: Storm; onClose: () => void }) {
  const [meso, setMeso] = useState<Frame | null>(null);
  useEffect(() => {
    setMeso(null);
    if (!s.meso[0]) return;
    api.frames({ source: "GOES-18", sector: s.meso[0], product: IR, overlay: true, hours: 2 }, 1)
      .then((f) => setMeso(f.at(-1) ?? null)).catch(() => {});
  }, [s.storm_id, s.meso]);
  const radii = (r: Record<string, number[]> | undefined) =>
    (["64", "50", "34"] as const).filter((k) => r?.[k]).map((k) => `${k} kt: ${r![k].join("/")}`).join(" · ");
  return (
    <Panel bar={<span>{s.storm_id} · {s.basin}</span>} bodyClass="stormpanel" onClose={onClose}>
      <h2><span className="catbadge2" style={{ background: CAT_COLOR[s.category] }}>{s.category}</span>
        {s.kind} {s.name}</h2>
      <div className="times">Advisory {s.number} · {fmtTime(s.issued)}{s.final ? " · last advisory" : ""}</div>
      <div className="stats">
        <div><span className="dim">Position</span>{ll(s.lat, s.lon)}</div>
        <div><span className="dim">Max wind</span>{s.wind_kt} kt ({mph(s.wind_kt)}), gusts {s.gust_kt} kt</div>
        <div><span className="dim">Pressure</span>{s.pressure_mb} mb</div>
        <div><span className="dim">Moving</span>
          {s.move_dir != null ? `${compass(s.move_dir)} (${s.move_dir}°) at ${s.move_kt} kt` : "stationary"}</div>
        <div><span className="dim">Category</span>{catLabel(s.category)}</div>
        <div><span className="dim">Wind radii (NE/SE/SW/NW nm)</span>{radii(s.radii) || "—"}</div>
      </div>
      {meso && (
        <Link className="mesothumb" to={`/satellite?${new URLSearchParams({ src: "GOES-18", sector: s.meso[0], product: IR })}`}>
          <img src={meso.thumb} alt={s.meso[0]} />
          <span>{s.meso[0]} · {ago(meso.scan_time)} · loop →</span>
        </Link>
      )}
      <h3>Forecast</h3>
      <table className="fcsttable">
        <thead><tr><th>Valid</th><th>Position</th><th>Wind</th><th /></tr></thead>
        <tbody>
          {s.forecast.map((p: TcPoint) => (
            <tr key={p.valid} className={p.outlook ? "outlook" : ""}>
              <td>{zday(p.valid)}</td>
              <td>{ll(p.lat, p.lon)}</td>
              <td>{p.wind_kt != null ? `${p.wind_kt} kt` : "—"}</td>
              <td>{p.status ? <span className="dim">{p.status.toLowerCase()}</span>
                : <span className="catbadge2 sm" style={{ background: CAT_COLOR[p.category ?? "?"] }}>{p.category}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="dim small">Shaded rows are NHC's extended outlook (days 4–5), where track and intensity
        errors are larger. Points exactly as issued; NHC's cone is not in the text products, so none is drawn.</p>
      <h3>Products</h3>
      {s.products.map((p) => (
        <Link key={p.id} className="pick" to={`/text/${p.id}`}>{p.title} <span className="dim">{p.awips} · {ago(p.received)}</span></Link>
      ))}
    </Panel>
  );
}
