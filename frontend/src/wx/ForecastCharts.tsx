import { useState } from "react";
import type { Forecast } from "../api";
import { leave, niceTicks, useWidth } from "../chart";

// The point forecast over its 7 days as two small multiples on one time
// axis (never two y-scales on one chart): temperature and dew point (°F,
// 2px lines, legend plus end labels), and 12-hour chance of precipitation
// (bars over the period they cover). One crosshair runs through both.
// Series colours were run through the dataviz palette validator against
// the dark panel and a white embed host.
const TEMP = "#d95926", DEW = "#199e70", POP = "#3987e5";

type Pt = { t: number; temp?: number; dewpt?: number };

export default function ForecastCharts({ f }: { f: Forecast }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const unhover = leave(setHover);
  const p = f.point!;
  const off = p.utc_offset_h * 3600_000;
  const pts: Pt[] = p.series.map((e) => ({ t: Date.parse(e.t), temp: e.temp, dewpt: e.dewpt }))
    .filter((e) => e.temp != null || e.dewpt != null);
  if (pts.length < 2) return null;
  const now = Date.now();
  const t0 = Math.max(pts[0].t, now - 3 * 3600_000), t1 = pts.at(-1)!.t;
  const shown = pts.filter((e) => e.t >= t0 - 6 * 3600_000);
  const m = { l: 40, r: 44, t: 10, b: 24 };
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0)) * (w - m.l - m.r);
  // Local midnights for the day ticks and day labels at local noon.
  const days: number[] = [];
  for (let d = Math.floor((t0 + off) / 86_400_000) * 86_400_000 - off; d <= t1; d += 86_400_000) days.push(d);
  const dayLabel = (t: number) => new Date(t + off).toLocaleDateString([], { weekday: "short", timeZone: "UTC" });

  const near = (t: number) => shown.reduce((b, e) => (Math.abs(e.t - t) < Math.abs(b.t - t) ? e : b), shown[0]);
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(near(t0 + ((e.clientX - r.left - m.l) / (w - m.l - m.r)) * (t1 - t0)).t);
  };
  const hp = hover != null ? shown.find((e) => e.t === hover) : undefined;
  const local = (t: number) => new Date(t + off).toLocaleString([], { weekday: "short", hour: "numeric", timeZone: "UTC" });

  // Temperature chart
  const th = 190;
  const vals = shown.flatMap((e) => [e.temp, e.dewpt]).filter((v): v is number => v != null);
  const vlo = Math.min(...vals) - 2, vhi = Math.max(...vals) + 2;
  const yt = niceTicks(vlo, vhi, 4);
  const y0 = Math.min(yt[0] ?? vlo, vlo), y1 = Math.max(yt.at(-1) ?? vhi, vhi);
  const y = (v: number) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (th - m.t - m.b);
  const line = (k: "temp" | "dewpt") => shown.filter((e) => e[k] != null && e.t >= t0 - 1)
    .map((e, i) => `${i ? "L" : "M"}${x(Math.max(e.t, t0))},${y(e[k]!)}`).join("");
  const last = (k: "temp" | "dewpt") => [...shown].reverse().find((e) => e[k] != null);
  const lt = last("temp"), ld = last("dewpt");
  // Keep the two end labels apart.
  let ly = lt ? y(lt.temp!) : 0, ldy = ld ? y(ld.dewpt!) : 0;
  if (lt && ld && Math.abs(ly - ldy) < 12) { const mid = (ly + ldy) / 2; ly = mid - 6; ldy = mid + 6; }

  // Chance of precipitation: one bar per 12-hour period, drawn over it.
  const ph = 110;
  const periods = p.twelve.filter((x) => x.pop != null).map((x) => ({ end: Date.parse(x.t), pop: x.pop! }))
    .filter((x) => x.end > t0);
  const py = (v: number) => m.t + (1 - v / 100) * (ph - m.t - m.b);
  const hoverPop = hover != null ? periods.find((x) => hover > x.end - 12 * 3600_000 && hover <= x.end) : undefined;

  const grid = (h: number) => days.map((d) => d > t0 && (
    <line key={d} x1={x(d)} x2={x(d)} y1={m.t} y2={h - m.b} className="grid" />
  ));
  const labels = (h: number) => days.map((d) => {
    const mid = Math.max(t0, d) + (Math.min(t1, d + 86_400_000) - Math.max(t0, d)) / 2;
    return Math.min(t1, d + 86_400_000) - Math.max(t0, d) > 6 * 3600_000 &&
      <text key={d} x={x(mid)} y={h - 6} textAnchor="middle" className="tick">{dayLabel(d + 12 * 3600_000)}</text>;
  });
  const cross = (h: number) => hover != null && (
    <line x1={x(hover)} x2={x(hover)} y1={m.t} y2={h - m.b} className="cross" />
  );
  const svgProps = { onPointerMove: onMove, onPointerDown: onMove, onPointerLeave: unhover };

  return (
    <div className="fccharts">
      <figure className="sigchart">
        <figcaption>
          <b>Temperature</b> <span className="legend-row-h inline">
            <span><i style={{ background: TEMP }} />Temperature</span><span><i style={{ background: DEW }} />Dew point</span>
          </span>
        </figcaption>
        <div className="plot" ref={ref}>
          <svg width={w} height={th} role="img" {...svgProps}
            aria-label={`Temperature from ${Math.min(...vals)} to ${Math.max(...vals)} °F over the next ${days.length} days`}>
            {yt.map((v) => (
              <g key={v}>
                <line x1={m.l} x2={w - m.r} y1={y(v)} y2={y(v)} className="grid" />
                <text x={m.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{v}°</text>
              </g>
            ))}
            {grid(th)}
            {labels(th)}
            <path d={line("dewpt")} fill="none" stroke={DEW} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            <path d={line("temp")} fill="none" stroke={TEMP} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {lt && <text x={w - m.r + 6} y={ly + 4} className="tick endlabel">{lt.temp}°</text>}
            {ld && <text x={w - m.r + 6} y={ldy + 4} className="tick endlabel">{ld.dewpt}°</text>}
            {cross(th)}
            {hp?.temp != null && <circle cx={x(hp.t)} cy={y(hp.temp)} r={4} fill={TEMP} stroke="var(--panel)" strokeWidth={2} />}
            {hp?.dewpt != null && <circle cx={x(hp.t)} cy={y(hp.dewpt)} r={4} fill={DEW} stroke="var(--panel)" strokeWidth={2} />}
          </svg>
          {hp && (
            <div className="sigtip" style={{ left: Math.min(x(hp.t) + 10, w - 170) }}>
              <div className="dim">{local(hp.t)}</div>
              {hp.temp != null && <div><span className="key" style={{ background: TEMP }} />Temperature <b>{hp.temp}°</b></div>}
              {hp.dewpt != null && <div><span className="key" style={{ background: DEW }} />Dew point <b>{hp.dewpt}°</b></div>}
            </div>
          )}
        </div>
      </figure>
      {periods.length > 0 && (
        <figure className="sigchart">
          <figcaption><b>Chance of precipitation</b> <span className="dim">per 12 hours</span></figcaption>
          <div className="plot">
            <svg width={w} height={ph} role="img" {...svgProps}
              aria-label={`Chance of precipitation, highest ${Math.max(...periods.map((x) => x.pop))}%`}>
              {[0, 50, 100].map((v) => (
                <g key={v}>
                  <line x1={m.l} x2={w - m.r} y1={py(v)} y2={py(v)} className="grid" />
                  <text x={m.l - 6} y={py(v) + 4} textAnchor="end" className="tick">{v}%</text>
                </g>
              ))}
              {grid(ph)}
              {labels(ph)}
              {periods.map((q) => {
                const a = x(Math.max(t0, q.end - 12 * 3600_000)) + 1, b = x(q.end) - 1;
                const top = py(q.pop), base = py(0);
                if (b - a < 2 || q.pop <= 0) return null;
                const r = Math.min(4, (b - a) / 2, base - top);
                return <path key={q.end} fill={POP} opacity={hoverPop === q ? 1 : 0.85}
                  d={`M${a},${base}V${top + r}Q${a},${top} ${a + r},${top}H${b - r}Q${b},${top} ${b},${top + r}V${base}Z`} />;
              })}
              {cross(ph)}
            </svg>
            {hover != null && hoverPop && (
              <div className="sigtip" style={{ left: Math.min(x(hover) + 10, w - 170) }}>
                <b>{hoverPop.pop}%</b> <span className="dim">chance, 12 h to {local(hoverPop.end)}</span>
              </div>
            )}
          </div>
        </figure>
      )}
      <details className="tableview">
        <summary>Data table</summary>
        <table className="sigtable">
          <thead><tr><th>Time</th><th>Temp</th><th>Dew point</th></tr></thead>
          <tbody>{shown.map((e) => <tr key={e.t}><td>{local(e.t)}</td><td>{e.temp ?? "—"}°</td><td>{e.dewpt ?? "—"}°</td></tr>)}</tbody>
        </table>
      </details>
    </div>
  );
}
