import OpenSeadragon from "openseadragon";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { ago, api, fmtTime, type Catalog, type Frame, type ImageSource } from "../api";
import { useLiveEvents } from "../events";

// Image loops: satellite imagery in its native (geostationary) view on
// the Satellite tab, NWS radar mosaics and charts on Radar & charts.
// Every frame of the loop is an image in one OpenSeadragon world,
// preloaded, and playing just switches which one is opaque: no flicker,
// and pan/zoom carry over from frame to frame. The viewer is created
// once; selections only change what is in its world (rebuilding it would
// reset the view).

const RANGES = [1, 3, 6, 12, 24];
const SPEEDS = [2, 4, 8, 12];
const MAX_FRAMES = 96;
// satweather-imaging caps display copies at this; a copy this wide has a
// larger original behind it.
const DISPLAY_PX = 2048;
const SECTOR_SHORT: Record<string, string> = { "Mesoscale 1": "Meso 1", "Mesoscale 2": "Meso 2" };

type Group = ImageSource["group"];
type Sel = { source: string; sector: string; product: string; overlay: boolean; hours: number };

// Satellites default to infrared: the visible composites (False Color)
// are black for half of every day. Radar to CONUS, charts to the national
// hazards map.
function defaultProduct(src: ImageSource): string {
  const prefer = src.group === "nws" ? [/^RADREFUS$/, /^IMGWWAUS$/] : [/^Clean_Longwave/i];
  const pool = src.group === "nws" ? src.products : src.products.filter((p) => p.kind === "rgb");
  for (const re of prefer) {
    const hit = pool.find((p) => re.test(p.product));
    if (hit) return hit.product;
  }
  return (pool[0] ?? src.products[0])?.product ?? "";
}

// Charts change a few times a day; loops of them want a longer range.
const defaultHours = (src: ImageSource) => (src.sector === "Charts" ? 24 : 3);

const KIND_HEADING: Record<string, string> = { rgb: "Composites", band: "Bands" };

export default function LoopView({ group }: { group: Group }) {
  const [sp, setSp] = useSearchParams();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(4);
  const [, setTick] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const viewer = useRef<OpenSeadragon.Viewer | null>(null);
  const items = useRef(new Map<number, OpenSeadragon.TiledImage>());
  const shown = useRef<number | null>(null);
  const loadedIds = useRef(new Set<number>());
  const framesRef = useRef(frames);
  framesRef.current = frames;
  const idxRef = useRef(idx);
  idxRef.current = idx;
  const playingRef = useRef(playing);
  playingRef.current = playing;

  // Full resolution: paused and zoomed in past the display copy, the
  // frame's original (/orig, the share) is laid exactly over the copy.
  // Stepping while zoomed brings each frame's original in; playing or a
  // new selection drops it. No tiles to build or store: the LAN moves a
  // 5-30 MB PNG in well under a second.
  const [full, setFull] = useState<"off" | "loading" | "on">("off");
  const hi = useRef<{ id: number; item: OpenSeadragon.TiledImage | null } | null>(null);

  const dropFull = useCallback(() => {
    if (hi.current?.item) viewer.current?.world.removeItem(hi.current.item);
    hi.current = null;
    setFull("off");
  }, []);

  const maybeFull = useCallback(() => {
    const v = viewer.current;
    const f = framesRef.current[idxRef.current];
    const base = f && items.current.get(f.id);
    if (!v || !base || playingRef.current || !f.orig || f.width < DISPLAY_PX || hi.current?.id === f.id) return;
    // Image zoom is display-copy pixels per screen pixel; past 1 the copy
    // is being magnified (device pixels, so retina screens count).
    if (base.viewportToImageZoom(v.viewport.getZoom(true)) * devicePixelRatio <= 1.1) return;
    if (hi.current?.item) v.world.removeItem(hi.current.item);
    hi.current = { id: f.id, item: null };
    setFull("loading");
    const b = base.getBounds();
    v.addTiledImage({
      tileSource: { type: "image", url: f.orig, buildPyramid: false },
      x: b.x, y: b.y, width: b.width,
      success: (e) => {
        const item = (e as unknown as { item: OpenSeadragon.TiledImage }).item;
        if (hi.current?.id !== f.id) return void v.world.removeItem(item);
        hi.current.item = item;
        const on = () => { if (hi.current?.item === item) setFull("on"); };
        if (item.getFullyLoaded()) on();
        else item.addOnceHandler("fully-loaded-change", on);
      },
      // The share keeps originals for a while only; the copy stays.
      error: () => { if (hi.current?.id === f.id) { hi.current = null; setFull("off"); } },
    });
  }, []);

  useEffect(() => {
    api.imageCatalog()
      .then((c) => { setCat({ ...c, sources: c.sources.filter((s) => s.group === group) }); setErr(null); })
      .catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Selection lives in the URL, so a loop can be linked and survives reload.
  const sel: Sel | null = useMemo(() => {
    if (!cat?.sources.length) return null;
    const src = cat.sources.find((s) => s.source === sp.get("src") && s.sector === sp.get("sector"))
      ?? cat.sources[0];
    const product = src.products.find((p) => p.product === sp.get("product"))?.product ?? defaultProduct(src);
    const p = src.products.find((x) => x.product === product);
    const wantBorders = sp.get("borders") !== "0";
    const overlay = p ? (p.overlay && (wantBorders || !p.plain)) : false;
    const hours = Number(sp.get("hours")) || defaultHours(src);
    return { source: src.source, sector: src.sector, product, overlay, hours };
  }, [cat, sp]);

  const source = cat?.sources.find((s) => s.source === sel?.source && s.sector === sel?.sector);
  const prod = source?.products.find((p) => p.product === sel?.product);

  const choose = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) v === null ? next.delete(k) : next.set(k, v);
    setSp(next, { replace: true });
  };

  useEffect(() => {
    if (!el.current) return;
    const v = OpenSeadragon({
      element: el.current,
      // The WebGL drawer samples a single full-size image without mipmaps,
      // so a 2048 px disk shown at 800 px gets stair-stepped edges; the
      // canvas drawer smooths when scaling down.
      drawer: "canvas",
      showNavigationControl: false,
      blendTime: 0,
      immediateRender: true,
      animationTime: 0.35,
      maxZoomPixelRatio: 4,
      visibilityRatio: 0.5,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
    });
    v.addHandler("animation-finish", maybeFull);
    viewer.current = v;
    return () => {
      v.destroy();
      viewer.current = null;
      items.current.clear();
      loadedIds.current.clear();
    };
  }, []);

  // Load the loop for the selection.
  const key = sel && `${sel.source}|${sel.sector}|${sel.product}|${sel.overlay}|${sel.hours}`;
  useEffect(() => {
    if (!sel) return;
    let stale = false;
    api.frames({ ...sel }).then((f) => {
      if (stale) return;
      setErr(null);
      setFrames(f.slice(-MAX_FRAMES));
      setIdx(Math.max(0, Math.min(f.length, MAX_FRAMES) - 1));
    }).catch((e) => setErr(String(e)));
    return () => { stale = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Show the current frame, hide the one before.
  const sync = useCallback(() => {
    const f = framesRef.current[idxRef.current];
    const item = f && items.current.get(f.id);
    if (!item) return;
    if (shown.current !== null && shown.current !== f.id) items.current.get(shown.current)?.setOpacity(0);
    item.setOpacity(1);
    shown.current = f.id;
  }, []);

  // Keep the viewer's world in step with `frames`. Images still loading
  // for a previous selection are dropped when they arrive.
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    const want = new Set(frames.map((f) => f.id));
    for (const [id, item] of items.current) {
      if (want.has(id)) continue;
      v.world.removeItem(item);
      items.current.delete(id);
      loadedIds.current.delete(id);
      if (shown.current === id) shown.current = null;
    }
    const empty = !items.current.size;
    for (const f of frames) {
      if (items.current.has(f.id)) continue;
      v.addTiledImage({
        tileSource: { type: "image", url: f.display, buildPyramid: false },
        opacity: 0,
        preload: true,
        success: (e) => {
          const item = (e as unknown as { item: OpenSeadragon.TiledImage }).item;
          if (!framesRef.current.some((x) => x.id === f.id) || items.current.has(f.id)) {
            v.world.removeItem(item);
            return;
          }
          items.current.set(f.id, item);
          const done = () => { loadedIds.current.add(f.id); setTick((t) => t + 1); };
          if (item.getFullyLoaded()) done();
          else item.addOnceHandler("fully-loaded-change", done);
          if (empty && items.current.size === 1) v.viewport.goHome(true);
          sync();
        },
      });
    }
  }, [frames, sync]);

  useEffect(sync, [idx, frames, sync]);

  useEffect(() => {
    const f = frames[idx];
    if (playing || (hi.current && hi.current.id !== f?.id)) dropFull();
    if (!playing) maybeFull();
  }, [idx, playing, frames, dropFull, maybeFull]);

  // Play: step at `fps`, holding the newest frame a little longer.
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    let hold = 0;
    const t = setInterval(() => {
      setIdx((i) => {
        if (i === frames.length - 1 && hold++ < Math.max(2, fps)) return i;
        hold = 0;
        return (i + 1) % frames.length;
      });
    }, 1000 / fps);
    return () => clearInterval(t);
  }, [playing, fps, frames.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, select, textarea")) return;
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
      else if (e.key === "ArrowLeft") { setPlaying(false); setIdx((i) => Math.max(0, i - 1)); }
      else if (e.key === "ArrowRight") { setPlaying(false); setIdx((i) => Math.min(frames.length - 1, i + 1)); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [frames.length]);

  // New frames for this loop arrive as the imaging worker makes them.
  useLiveEvents((e) => {
    if (e.type !== "frame" || !sel) return;
    if (e.source !== sel.source || e.sector !== sel.sector || e.product !== sel.product
        || e.overlay !== sel.overlay) return;
    const f: Frame = { id: e.id, scan_time: e.scan_time, display: `/media/${e.display}`, thumb: `/media/${e.thumb}`,
      width: e.width, height: e.height, orig: e.orig };
    if (frames.some((x) => x.id === f.id)) return;
    const cutoff = Date.now() - sel.hours * 3600_000;
    const next = [...frames, f].filter((x) => new Date(x.scan_time).getTime() >= cutoff)
      .sort((a, b) => a.scan_time.localeCompare(b.scan_time)).slice(-MAX_FRAMES);
    // Follow the newest frame unless an older one is being looked at;
    // then stay on the same frame as older ones drop off the front.
    const atEnd = idx >= frames.length - 1;
    const dropped = frames.length + 1 - next.length;
    setFrames(next);
    setIdx(atEnd ? next.length - 1 : Math.max(0, idx - dropped));
    setCat((c) => c && { ...c, sources: c.sources.map((s) => s.source === e.source && s.sector === e.sector
      ? { ...s, last: e.scan_time > s.last ? e.scan_time : s.last } : s) });
  });

  const cur = frames[idx];
  const loaded = frames.filter((f) => loadedIds.current.has(f.id)).length;

  return (
    <div className="satview">
      <aside className="satside">
        <div className="satgroup">
          {cat?.sources.map((s) => (
            <button key={`${s.source}|${s.sector}`}
              className={`srcbtn${s.source === sel?.source && s.sector === sel?.sector ? " on" : ""}`}
              onClick={() => choose({ src: s.source, sector: s.sector, product: null, hours: null })}>
              <span>{group === "satellite" && `${s.source} `}<b>{SECTOR_SHORT[s.sector] ?? s.sector}</b></span>
              <span className="n">{ago(s.last)}</span>
            </button>
          ))}
          {cat && !cat.sources.length && (
            <p className="empty">No images yet{cat.queue.pending ? ` (${cat.queue.pending} being made)` : ""}.</p>
          )}
        </div>
        {source && (
          <div className="satgroup">
            {(["rgb", "band", "graphic"] as const).map((k) => {
              const ps = source.products.filter((p) => p.kind === k);
              return ps.length > 0 && (
                <div key={k} className="prodgroup">
                  {KIND_HEADING[k] && <div className="hint">{KIND_HEADING[k]}</div>}
                  {ps.map((p) => (
                    <button key={p.product} className={`prodbtn${p.product === sel?.product ? " on" : ""}`}
                      onClick={() => choose({ product: p.product })}>{p.label}</button>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </aside>
      <section className="satmain">
        <div ref={el} className="osd" />
        {err && <div className="maperr">{err}</div>}
        {sel && !frames.length && !err && <div className="satnote">No frames in the last {sel.hours} h.</div>}
        <div className="satbar">
          <button className="ctl" onClick={() => setPlaying((p) => !p)} disabled={frames.length < 2}
            title="Play/pause (space)">{playing ? "❚❚" : "▶"}</button>
          <input className="scrub" type="range" min={0} max={Math.max(0, frames.length - 1)} value={idx}
            onChange={(e) => { setPlaying(false); setIdx(Number(e.target.value)); }} />
          <span className="sattime">
            {cur ? <>{fmtTime(cur.scan_time)} <span className="dim">· {idx + 1}/{frames.length}</span></> : "—"}
            {loaded < frames.length && <span className="dim"> · loading {loaded}/{frames.length}</span>}
          </span>
          {full !== "off" && (
            <span className="fullres">{full === "loading" ? "Loading full resolution…" : "Full resolution"}</span>
          )}
          {prod?.overlay && prod.plain && (
            <label className="satopt">
              <input type="checkbox" checked={sel!.overlay}
                onChange={(e) => choose({ borders: e.target.checked ? null : "0" })} /> Borders
            </label>
          )}
          <select value={sel?.hours ?? 3} onChange={(e) => choose({ hours: e.target.value })} title="Loop length">
            {RANGES.map((h) => <option key={h} value={h}>{h} h</option>)}
          </select>
          <select value={fps} onChange={(e) => setFps(Number(e.target.value))} title="Frames per second">
            {SPEEDS.map((s) => <option key={s} value={s}>{s} fps</option>)}
          </select>
          <button className="ctl" onClick={() => viewer.current?.viewport.goHome()} title="Reset zoom">⤢</button>
        </div>
      </section>
    </div>
  );
}
