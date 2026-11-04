import { useState } from "react";
import type { Day, Forecast, Half, Hour, Later, Tilt } from "../api";
import WxIcon from "./WxIcon";
import { reflow } from "./ZonePanel";

// Day panels in the weather-app way: icon, high/low, condition, rain
// chance and wind for each of the PFM's 7 days, a high-low range bar
// against the week, and (on the Forecast tab) the day's hours and its zone
// forecast text on a tap. Days 8-10 are CPC outlook panels when the LLM
// read them out of the 6-10/8-14 day discussion; CPC gives odds of warmer or
// cooler than normal, not temperatures, so those panels carry no numbers.

const DEG: Record<string, number> = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 };
const WCHAR: Record<string, string> = { LT: "Light", GN: "Gentle", BZ: "Breezy", WY: "Windy", VW: "Very windy",
  SD: "Strong wind", HF: "Hurricane force" };

export function localToday(offsetH: number): string {
  return new Date(Date.now() + offsetH * 3600_000).toISOString().slice(0, 10);
}

const noon = (d: string) => new Date(`${d}T12:00:00Z`);
const weekday = (d: string, style: "short" | "long" = "short") =>
  noon(d).toLocaleDateString([], { weekday: style, timeZone: "UTC" });
const monthDay = (d: string) => noon(d).toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" });

function dayName(r: Day, today: string): string {
  if (r.date === today) return r.day ? "Today" : "Tonight";
  return weekday(r.date);
}

export function Wind({ h, small }: { h: Pick<Half, "wind_dir" | "wind_spd" | "wind_gust" | "wind_char">; small?: boolean }) {
  const deg = h.wind_dir != null ? DEG[h.wind_dir] : undefined;
  // The arrow points where the wind blows to; the label says where it's from.
  const arrow = deg != null && (
    <svg className="windarrow" viewBox="0 0 16 16" width={small ? 11 : 13} height={small ? 11 : 13}
      style={{ transform: `rotate(${deg + 180}deg)` }} aria-hidden>
      <path d="M8 1v12M3.5 8.5L8 14l4.5-5.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  );
  const what = h.wind_char && WCHAR[h.wind_char] ? WCHAR[h.wind_char]
    : h.wind_spd != null ? `${h.wind_dir ?? ""} ${h.wind_spd}`.trim() : null;
  if (!what) return null;
  return (
    <span className="wind" title={h.wind_dir ? `Wind from the ${h.wind_dir}` : undefined}>
      {arrow}{what}{h.wind_gust ? <span className="gust"> G{h.wind_gust}</span> : null}
      {!h.wind_char && h.wind_spd != null && !small ? <span className="dim"> mph</span> : null}
    </span>
  );
}

export function Drop({ pop }: { pop: number | null }) {
  if (pop == null) return null;
  return (
    <span className={`pop${pop < 20 ? " low" : ""}`} title={`${pop}% chance of precipitation`}>
      <svg viewBox="0 0 10 14" width={8} height={11} aria-hidden><path d="M5 1C3 5 1 7 1 9.5a4 4 0 0 0 8 0C9 7 7 5 5 1z"
        fill="var(--wx-rain)" /></svg>
      {pop}%
    </span>
  );
}

const TEMP_TILT: Record<Tilt, string> = { above: "Warmer than normal", near: "Near-normal temperatures",
  below: "Cooler than normal", unknown: "No clear signal" };
const PRECIP_TILT: Record<Tilt, string> = { above: "Wetter than normal", near: "Near-normal rain and snow",
  below: "Drier than normal", unknown: "" };

export function DayStrip({ f, max, compact }: { f: Forecast; max?: number; compact?: boolean }) {
  const later: Later[] = f.later ?? [];
  const today = localToday(f.point?.utc_offset_h ?? 0);
  const rows = f.daily.slice(0, max ?? f.daily.length);
  const [open, setOpen] = useState<string | null>(null);
  const temps = rows.flatMap((r) => [r.hi, r.lo]).filter((v): v is number => v != null);
  const wmin = Math.min(...temps), wmax = Math.max(...temps);
  const sel = rows.find((r) => r.date === open);

  return (
    <div className={`daystrip${compact ? " compact" : ""}`}>
      <div className="days" role="list">
        {rows.map((r) => {
          const main = r.day ?? r.night!;
          const c = main.condition;
          const lo = r.lo ?? r.hi, hi = r.hi ?? r.lo;
          const span = wmax - wmin || 1;
          return (
            <button key={r.date} role="listitem" className={`daycard${open === r.date ? " on" : ""}`}
              onClick={() => !compact && setOpen((o) => (o === r.date ? null : r.date))}
              aria-expanded={compact ? undefined : open === r.date}
              title={compact ? `${weekday(r.date, "long")}: ${c.text}` : "Show the day's hours"}>
              <div className="dname">{dayName(r, today)} <span className="dim">{monthDay(r.date)}</span></div>
              <div className="dicons">
                <WxIcon icon={c.icon} night={!r.day} size={compact ? 40 : 56} title={c.text} />
                {r.day && r.night && !compact && (
                  <span className="nighticon"><WxIcon icon={r.night.condition.icon} night size={26}
                    title={`Night: ${r.night.condition.text}`} /></span>
                )}
                {c.badge && <span className="badge-chance">{c.badge}</span>}
              </div>
              <div className="temps">
                {r.hi != null && <b>{r.hi}°</b>}
                {r.lo != null && <span className="lo">{r.lo}°</span>}
              </div>
              {!compact && hi != null && lo != null && (
                <div className="range" aria-hidden>
                  <i style={{ left: `${((lo - wmin) / span) * 100}%`, width: `${Math.max(4, ((hi - lo) / span) * 100)}%` }} />
                </div>
              )}
              <div className="cond">{c.text}</div>
              <div className="meta">
                <Drop pop={r.pop} />
                {!compact && <Wind h={main} />}
                {!compact && r.qpf ? <span className="qpf">{r.qpf} in</span> : null}
              </div>
            </button>
          );
        })}
        {!compact && later.map((d) => (
          <div key={d.date} role="listitem" className="daycard later"
            title={`CPC's ${d.period} day outlook for ${d.region}: odds, not a forecast temperature`}>
            <div className="dname">{weekday(d.date)} <span className="dim">{monthDay(d.date)}</span></div>
            <div className="tilts">
              <span className={`tilt t-${d.temp}`}><Thermo /> {TEMP_TILT[d.temp]}</span>
              {d.precip !== "unknown" && <span className={`tilt p-${d.precip}`}>
                <svg viewBox="0 0 10 14" width={9} height={12} aria-hidden><path
                  d="M5 1C3 5 1 7 1 9.5a4 4 0 0 0 8 0C9 7 7 5 5 1z" fill="var(--wx-rain)" /></svg> {PRECIP_TILT[d.precip]}</span>}
            </div>
            <div className="cond dim">CPC outlook</div>
          </div>
        ))}
      </div>
      {sel && <DayDetail r={sel} hours={f.hours[sel.date] ?? []} zfp={f.forecast?.body} today={today} />}
    </div>
  );
}

function Thermo() {
  return (
    <svg viewBox="0 0 12 20" width={9} height={15} aria-hidden>
      <path d="M4 3a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z" fill="none" stroke="currentColor" strokeWidth={1.6} />
      <circle cx={6} cy={15} r={2.2} fill="currentColor" />
    </svg>
  );
}

const TODAY_LABELS = /^(today|this (morning|afternoon|evening)|rest of (today|tonight)|tonight|overnight|late tonight)\b/i;

function hourLabel(local: string): string {
  const h = Number(local.slice(0, 2));
  return h === 0 ? "12 AM" : h === 12 ? "Noon" : h < 12 ? `${h} AM` : `${h - 12} PM`;
}

function DayDetail({ r, hours, zfp, today }: { r: Day; hours: Hour[]; zfp?: string; today: string }) {
  const name = weekday(r.date, "long").toLowerCase();
  const paras = zfp ? reflow(zfp).filter((p) => p.label && !p.pre && (
    (r.date === today && TODAY_LABELS.test(p.label)) || p.label.toLowerCase().startsWith(name))) : [];
  return (
    <div className="daydetail">
      <h3>{weekday(r.date, "long")} <span className="dim">{monthDay(r.date)}</span></h3>
      {hours.length > 0 && (
        <div className="hours" role="list">
          {hours.map((h) => (
            <div key={h.t} className="hour" role="listitem">
              <div className="dim">{hourLabel(h.local)}</div>
              <WxIcon icon={h.condition.icon} night={h.condition.night} size={30} title={h.condition.text} />
              <b>{h.temp != null ? `${h.temp}°` : "—"}</b>
              <Drop pop={h.pop} />
              <Wind h={h} small />
            </div>
          ))}
        </div>
      )}
      {paras.length > 0 && (
        <div className="fcst">
          {paras.map((p, i) => <p key={i}><b>{p.label}: </b>{p.text}</p>)}
        </div>
      )}
    </div>
  );
}
