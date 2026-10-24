import { useEffect, useMemo, useRef, useState } from "react";
import { ago, api, type SignalBucket, type SignalData, type SignalSample } from "../api";
import { useLiveEvents } from "../events";

// The receiver's signal, from SatDump's live stats (satweather-signal polls
// them every 10 s), and what came through it. Charts follow the dataviz
// method: one series per chart and one axis, 2px lines with a 10% band,
// hairline grid, crosshair tooltips; the lock strip uses the fixed status
// colours with a legend; the throughput stack's three hues were run
// through the palette validator against this surface.

const RANGES = [{ h: 6, label: "6 hours" }, { h: 24, label: "24 hours" }, { h: 168, label: "7 days" }];
const LINE = "#4fb3ff";
const STATUS = { good: "#0ca30c", partial: "#fab219", lost: "#d03b3b", silent: "#5c6876" };
const SERIES = [
  { key: "text", label: "Text bulletins", color: "#3987e5" },
  { key: "graphics", label: "NWS graphics", color: "#d95926" },
  { key: "images", label: "Satellite images", color: "#199e70" },
] as const;

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const fmtClock = (t: number, hours: number) => {
  const d = new Date(t);
  return hours > 48
    ? d.toLocaleDateString([], { weekday: "short", day: "numeric" })
    : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};

// Hourly steps, widened until the labels have room (about 64 px each).
function timeTicks(t0: number, t1: number, hours: number, px: number): number[] {
  const fit = Math.max(2, Math.floor(px / 64));
  const step = ([1, 2, 3, 6, 12, 24, 48].find((h) => hours / h <= fit) ?? 48) * 3600_000;
  const out: number[] = [];
  const start = Math.ceil(t0 / step) * step;
  for (let t = start; t <= t1; t += step) out.push(t);
  return out;
}

function niceTicks(lo: number, hi: number, n = 4): number[] {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(6));
  return out;
}

type Pt = { t: number; y: number | null; lo?: number | null; hi?: number | null };

// A single-series time line with an optional min-max band and a crosshair.
function LineChart({ points, hours, t0, t1, unit, digits, title, note }: {
  points: Pt[]; hours: number; t0: number; t1: number; unit: string; digits: number; title: string; note: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Pt | null>(null);
  const h = 180, m = { l: 48, r: 12, t: 10, b: 26 };
  const vals = points.flatMap((p) => [p.y, p.lo, p.hi]).filter((v): v is number => v != null);
  const lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  const yt = niceTicks(Math.min(lo, hi - 1e-6), hi);
  const y0 = Math.min(yt[0] ?? lo, lo), y1 = Math.max(yt.at(-1) ?? hi, hi);
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0)) * (w - m.l - m.r);
  const y = (v: number) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (h - m.t - m.b);
  // Break the line where there is no data, rather than bridging the gap.
  const segs: Pt[][] = [];
  for (const p of points) {
    if (p.y == null) { segs.push([]); continue; }
    if (!segs.length || !segs.at(-1)!.length || p.t - segs.at(-1)!.at(-1)!.t > 3 * gap(points)) segs.push([]);
    segs.at(-1)!.push(p);
  }
  const path = (s: Pt[], k: "y" | "lo" | "hi") => s.map((p, i) => `${i ? "L" : "M"}${x(p.t)},${y(p[k] as number)}`).join("");
  const band = (s: Pt[]) => s.length && s[0].lo != null
    ? `${path(s, "hi")}L${s.slice().reverse().map((p) => `${x(p.t)},${y(p.lo as number)}`).join("L")}Z` : "";
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - r.left - m.l) / (w - m.l - m.r)) * (t1 - t0);
    let best: Pt | null = null;
    for (const p of points) if (p.y != null && (!best || Math.abs(p.t - t) < Math.abs(best.t - t))) best = p;
    setHover(best);
  };
  return (
    <figure className="sigchart">
      <figcaption><b>{title}</b> <span className="dim">{note}</span></figcaption>
      <div ref={ref} className="plot">
        <svg width={w} height={h} onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img"
          aria-label={`${title}, ${vals.length ? `from ${lo.toFixed(digits)} to ${hi.toFixed(digits)} ${unit}` : "no data"}`}>
          {yt.map((v) => (
            <g key={v}>
              <line x1={m.l} x2={w - m.r} y1={y(v)} y2={y(v)} className="grid" />
              <text x={m.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{v.toFixed(digits)}</text>
            </g>
          ))}
          {timeTicks(t0, t1, hours, w - m.l - m.r).map((t) => (
            <text key={t} x={x(t)} y={h - 6} textAnchor="middle" className="tick">{fmtClock(t, hours)}</text>
          ))}
          {segs.map((s, i) => s.length > 0 && (
            <g key={i}>
              {band(s) && <path d={band(s)} fill={LINE} opacity={0.1} />}
              <path d={path(s, "y")} fill="none" stroke={LINE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            </g>
          ))}
          {hover && (
            <g>
              <line x1={x(hover.t)} x2={x(hover.t)} y1={m.t} y2={h - m.b} className="cross" />
              <circle cx={x(hover.t)} cy={y(hover.y!)} r={4} fill={LINE} stroke="var(--panel)" strokeWidth={2} />
            </g>
          )}
        </svg>
        {hover && (
          <div className="sigtip" style={{ left: Math.min(x(hover.t) + 10, w - 170) }}>
            <b>{hover.y!.toFixed(digits)} {unit}</b>
            {hover.lo != null && <span className="dim"> range {hover.lo.toFixed(digits)}–{hover.hi!.toFixed(digits)}</span>}
            <div className="dim">{new Date(hover.t).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</div>
          </div>
        )}
      </div>
    </figure>
  );
}

const gap = (pts: Pt[]) => (pts.length > 1 ? (pts.at(-1)!.t - pts[0].t) / (pts.length - 1) : 60_000);

function syncState(b: SignalBucket): keyof typeof STATUS {
  if (b.reachable < 0.5 || b.locked == null) return "silent";
  return b.locked >= 0.99 ? "good" : b.locked > 0 ? "partial" : "lost";
}
const SYNC_LABEL = { good: "In sync", partial: "Dropping in and out", lost: "No sync", silent: "Receiver not answering" };

function SyncStrip({ buckets, hours, t0, t1, bucketS }: {
  buckets: SignalBucket[]; hours: number; t0: number; t1: number; bucketS: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<SignalBucket | null>(null);
  const m = { l: 48, r: 12 }, h = 22;
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0)) * (w - m.l - m.r);
  const bw = Math.max(1, x(t0 + bucketS * 1000) - x(t0));
  return (
    <figure className="sigchart">
      <figcaption><b>Frame sync</b> <span className="dim">share of samples with the deframer locked</span></figcaption>
      <div ref={ref} className="plot">
        <svg width={w} height={h + 22} onPointerLeave={() => setHover(null)} role="img"
          aria-label="Frame sync over time; see the legend and the data table">
          {buckets.map((b) => {
            const t = Date.parse(b.t);
            return (
              <rect key={b.t} x={x(t)} y={0} width={Math.max(1, bw - (bw > 4 ? 2 : 0))} height={h} rx={bw > 6 ? 2 : 0}
                fill={STATUS[syncState(b)]} onPointerEnter={() => setHover(b)} />
            );
          })}
          {timeTicks(t0, t1, hours, w - m.l - m.r).map((t) => (
            <text key={t} x={x(t)} y={h + 16} textAnchor="middle" className="tick">{fmtClock(t, hours)}</text>
          ))}
        </svg>
        {hover && (
          <div className="sigtip" style={{ left: Math.min(x(Date.parse(hover.t)) + 10, w - 190), top: 26 }}>
            <b>{SYNC_LABEL[syncState(hover)]}</b>
            {hover.locked != null && <span className="dim"> {Math.round(hover.locked * 100)}% locked</span>}
            <div className="dim">{new Date(hover.t).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</div>
          </div>
        )}
      </div>
      <div className="legend-row-h">
        {(Object.keys(STATUS) as (keyof typeof STATUS)[]).map((k) => (
          <span key={k}><i style={{ background: STATUS[k] }} />{SYNC_LABEL[k]}</span>
        ))}
      </div>
    </figure>
  );
}

function Throughput({ rows, hours }: { rows: SignalData["throughput"]; hours: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<SignalData["throughput"][number] | null>(null);
  const h = 180, m = { l: 48, r: 12, t: 10, b: 26 };
  const shown = rows.slice(-Math.min(rows.length, hours > 48 ? 168 : hours));
  const max = Math.max(1, ...shown.map((r) => r.text + r.graphics + r.images));
  const yt = niceTicks(0, max);
  const top = Math.max(yt.at(-1) ?? max, max);
  const slot = (w - m.l - m.r) / Math.max(1, shown.length);
  const bw = Math.min(24, Math.max(2, slot - 2));
  const y = (v: number) => m.t + (1 - v / top) * (h - m.t - m.b);
  const every = Math.ceil(shown.length / Math.max(1, Math.floor((w - m.l) / 70)));
  return (
    <figure className="sigchart">
      <figcaption><b>Files received per hour</b> <span className="dim">what SatDump wrote to the NAS</span></figcaption>
      <div className="legend-row-h">
        {SERIES.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
      </div>
      <div ref={ref} className="plot">
        <svg width={w} height={h} onPointerLeave={() => setHover(null)} role="img"
          aria-label="Files received per hour by kind; see the data table">
          {yt.map((v) => (
            <g key={v}>
              <line x1={m.l} x2={w - m.r} y1={y(v)} y2={y(v)} className="grid" />
              <text x={m.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{v >= 1000 ? `${v / 1000}K` : v}</text>
            </g>
          ))}
          {shown.map((r, i) => {
            const cx = m.l + slot * i + (slot - bw) / 2;
            let acc = 0;
            return (
              <g key={r.h} onPointerEnter={() => setHover(r)}>
                <rect x={m.l + slot * i} y={m.t} width={slot} height={h - m.t - m.b} fill="transparent" />
                {SERIES.map((s) => {
                  const v = r[s.key];
                  if (!v) return null;
                  const y1 = y(acc), y2 = y(acc + v);
                  acc += v;
                  return <rect key={s.key} x={cx} y={y2} width={bw} height={Math.max(0, y1 - y2 - 2)} fill={s.color} />;
                })}
                {i % every === 0 && (
                  <text x={cx + bw / 2} y={h - 6} textAnchor="middle" className="tick">{fmtClock(Date.parse(r.h), hours)}</text>
                )}
              </g>
            );
          })}
        </svg>
        {hover && (
          <div className="sigtip" style={{ left: Math.min(m.l + slot * shown.indexOf(hover) + 10, w - 180) }}>
            <b>{(hover.text + hover.graphics + hover.images).toLocaleString()} files</b>
            {SERIES.map((s) => (
              <div key={s.key}><i className="key" style={{ background: s.color }} />{hover[s.key].toLocaleString()} <span className="dim">{s.label.toLowerCase()}</span></div>
            ))}
            <div className="dim">{new Date(hover.h).toLocaleString([], { weekday: "short", hour: "numeric" })}</div>
          </div>
        )}
      </div>
    </figure>
  );
}

const db = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)} dB`);

export default function SignalView() {
  const [hours, setHours] = useState(24);
  const [d, setD] = useState<SignalData | null>(null);
  const [live, setLive] = useState<SignalSample[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let stale = false;
    const load = () => api.signal(hours).then((x) => { if (!stale) { setD(x); setErr(null); } })
      .catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, 60_000);
    return () => { stale = true; clearInterval(t); };
  }, [hours]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);
  useLiveEvents((e) => { if (e.type === "signal") setLive((l) => [...l, e].slice(-12)); });

  const cur = live.at(-1) ?? d?.latest ?? null;
  // Tiles show the last minute's average: one 10 s reading is noisy at low
  // SNR, and SatDump's estimate bottoms out at 0.0 dB.
  const minute = [...(d?.recent ?? []), ...live]
    .filter((x, i, all) => x.ok && all.findIndex((y) => y.t === x.t) === i && now - Date.parse(x.t) < 60_000);
  const mean = (k: "snr" | "viterbi_ber" | "freq_hz" | "rs_avg") => {
    const v = minute.map((x) => x[k]).filter((x): x is number => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  const t1 = now, t0 = now - hours * 3600_000;
  const pts = useMemo(() => (d?.history ?? []).map((b) => ({ t: Date.parse(b.t) + (d!.bucket_s * 500), b })), [d]);
  const answering = cur?.ok && Date.now() - Date.parse(cur.t) < 60_000;
  const locked = cur?.deframer_lock;

  if (d && !d.configured) {
    return <div className="signal"><p className="empty">The receiver's stats aren't configured on this server
      (SATWEATHER_SIGNAL_URL). Turn on SatDump's stats server on the receiver and point satweather at it.</p></div>;
  }
  return (
    <div className="signal">
      <div className="sigbar">
        <h1>Signal</h1>
        <div className="seg" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r.h} aria-pressed={hours === r.h} className={hours === r.h ? "on" : ""} onClick={() => setHours(r.h)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="empty">{err}</p>}

      <section className="tiles-row">
        <div className="hero-fig">
          <span className="label">Signal-to-noise ratio</span>
          <span className="value">{answering ? db(mean("snr")) : "—"}</span>
          <span className="dim">average over the last minute; latest {db(cur?.snr)}, peak {db(cur?.peak_snr)}</span>
        </div>
        <div className="tile">
          <span className="label">Frame sync</span>
          <span className={`value status ${!answering ? "silent" : locked ? "good" : "lost"}`}>
            <i aria-hidden="true">{!answering ? "–" : locked ? "✓" : "✕"}</i>
            {!answering ? "Not answering" : locked ? "In sync" : "No sync"}
          </span>
          <span className="dim">{cur?.viterbi_lock ? "Viterbi locked" : "Viterbi unlocked"}</span>
        </div>
        <div className="tile">
          <span className="label">Bit error rate</span>
          <span className="value">{answering && mean("viterbi_ber") != null ? mean("viterbi_ber")!.toFixed(3) : "—"}</span>
          <span className="dim">before error correction, last minute</span>
        </div>
        <div className="tile">
          <span className="label">Reed-Solomon fixes</span>
          <span className="value">{answering && cur!.rs_avg != null ? (cur!.rs_avg < 0 ? "Failing" : (mean("rs_avg") ?? 0).toFixed(1)) : "—"}</span>
          <span className="dim">byte errors corrected per block</span>
        </div>
        <div className="tile">
          <span className="label">Carrier offset</span>
          <span className="value">{answering && mean("freq_hz") != null ? `${mean("freq_hz")! > 0 ? "+" : ""}${Math.round(mean("freq_hz")!)} Hz` : "—"}</span>
          <span className="dim">{cur ? `updated ${ago(cur.t)}` : "no samples yet"}</span>
        </div>
      </section>
      {d?.last_ok && !answering && (
        <p className="parsenote">The receiver last answered {ago(d.last_ok)}. SatDump may be restarting, or rtl may be down.</p>
      )}

      <section className="charts">
        <LineChart title="Signal-to-noise ratio" note="average per interval, with its range"
          points={pts.map(({ t, b }) => ({ t, y: b.snr, lo: b.snr_min, hi: b.snr_max }))}
          hours={hours} t0={t0} t1={t1} unit="dB" digits={1} />
        <SyncStrip buckets={d?.history ?? []} hours={hours} t0={t0} t1={t1} bucketS={d?.bucket_s ?? 300} />
        <LineChart title="Bit error rate" note="Viterbi, before Reed-Solomon; lower is better"
          points={pts.map(({ t, b }) => ({ t, y: b.ber }))} hours={hours} t0={t0} t1={t1} unit="" digits={3} />
        <Throughput rows={d?.throughput ?? []} hours={hours} />
      </section>

      <section className="recv">
        <h2>Receiver</h2>
        {d?.receiver && Object.keys(d.receiver).length > 0 ? (
          <dl>
            <div><dt>Host</dt><dd>{d.receiver.host}</dd></div>
            <div><dt>Downlink</dt><dd>{(Number(d.receiver.frequency) / 1e6).toFixed(1)} MHz ({d.receiver.pipeline})</dd></div>
            <div><dt>Sample rate</dt><dd>{(Number(d.receiver.samplerate) / 1e6).toFixed(1)} million samples a second</dd></div>
            <div><dt>Gain</dt><dd>{d.receiver.gain} dB</dd></div>
            <div><dt>Bias-tee</dt><dd>{d.receiver.bias ? "On (powers the dish's amplifier)" : "Off"}</dd></div>
          </dl>
        ) : <p className="dim">Receiver settings aren't known to this server.</p>}
        <details>
          <summary>Data table</summary>
          <table className="sigtable">
            <thead><tr><th>Interval</th><th>SNR (dB)</th><th>Range</th><th>BER</th><th>Locked</th><th>Answering</th></tr></thead>
            <tbody>
              {(d?.history ?? []).slice().reverse().map((b) => (
                <tr key={b.t}>
                  <td>{new Date(b.t).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}</td>
                  <td>{b.snr?.toFixed(1) ?? "—"}</td>
                  <td>{b.snr_min != null ? `${b.snr_min.toFixed(1)}–${b.snr_max!.toFixed(1)}` : "—"}</td>
                  <td>{b.ber?.toFixed(3) ?? "—"}</td>
                  <td>{b.locked != null ? `${Math.round(b.locked * 100)}%` : "—"}</td>
                  <td>{Math.round(b.reachable * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </section>
    </div>
  );
}
