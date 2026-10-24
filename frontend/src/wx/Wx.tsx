import { useState } from "react";
import { ago, fmtTime, type Group, type MetarDecoded, type PirepDecoded, type Report, type TafDecoded,
  type WxSummary } from "../api";

// Decoded METAR / TAF / PIREP display. The decoding itself is Tachbase's
// (backend satweather/wxdecode.py); this only lays it out.

export const CAT_COLOR: Record<string, string> = {
  VFR: "#3cc36b", MVFR: "#3d8bfd", IFR: "#ff4d4d", LIFR: "#d65cff", UNK: "#6b7785",
};

export function FlightCat({ cat }: { cat?: string | null }) {
  const c = cat || "UNK";
  return <span className="fcat" style={{ background: CAT_COLOR[c] ?? CAT_COLOR.UNK }}>{c === "UNK" ? "—" : c}</span>;
}

// The decoder's summary has no wind; its group meanings do ("wind from
// 280° true at 16 kt"), so the chip is taken from there.
function windOf(groups?: Group[]) {
  const g = groups?.find((x) => /^(wind |calm)/.test(x.meaning));
  return g?.meaning.replace(/^wind from /, "").replace(" true", "");
}

function SummaryChips({ s, groups }: { s: WxSummary; groups?: Group[] }) {
  const wind = windOf(groups);
  return (
    <div className="chips">
      <FlightCat cat={s.flight_category} />
      {wind && <span>{wind}</span>}
      {s.visibility && <span>vis {s.visibility}</span>}
      {s.ceiling && <span>{s.ceiling.startsWith("none") ? "no ceiling" : `ceiling ${s.ceiling}`}</span>}
      {s.weather?.map((w) => <span key={w} className="wxchip">{w}</span>)}
    </div>
  );
}

function Groups({ groups, extra }: { groups: Group[]; extra?: string[] }) {
  return (
    <table className="groups">
      <tbody>
        {groups.map((g, i) => (
          <tr key={i}>
            <td className="g">{g.group}</td>
            <td>{g.meaning}</td>
          </tr>
        ))}
        {extra?.map((x, i) => (
          <tr key={`nd${i}`} className="nd">
            <td className="g">{x}</td>
            <td>not decoded</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MetarCard({ raw, decoded, time, compact = false }:
  { raw: string; decoded: MetarDecoded | null; time?: string | null; compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  return (
    <div className="wxcard">
      <div className="wxhead" onClick={() => setOpen((o) => !o)}>
        {decoded ? <SummaryChips s={decoded.summary} groups={decoded.decoded} /> : <FlightCat />}
        {time && <span className="dim when">{ago(time)}</span>}
      </div>
      <code className="wxraw">{raw}</code>
      {open && decoded && (
        <>
          <Groups groups={decoded.decoded} extra={decoded.not_decoded} />
          {decoded.remarks && decoded.remarks.length > 0 && (
            <>
              <div className="sub">Remarks</div>
              <Groups groups={decoded.remarks} />
            </>
          )}
        </>
      )}
    </div>
  );
}

// `expired`: say so (station panel, which checks valid_to). Inside a
// product the TAF is a record of what was forecast, so neither "now" nor
// "expired" is shown.
export function TafTimeline({ raw, decoded, current = true, expired = false }:
  { raw: string; decoded: TafDecoded; current?: boolean; expired?: boolean }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const now = decoded.at?.prevailing?.group;
  const alsoNow = new Set(decoded.at?.also_possible?.map((p) => p.group) ?? []);
  return (
    <div className="wxcard">
      {expired && <div className="dim">This TAF has expired.</div>}
      <code className="wxraw">{raw}</code>
      <div className="taf">
        {decoded.periods.map((p, i) => {
          const isNow = current && (p.group === now || alsoNow.has(p.group));
          const cat = p.summary.flight_category || "UNK";
          return (
            <div key={i} className={`tafp ${isNow ? "now" : ""} ${p.group.match(/^(TEMPO|PROB|BECMG|INTER)/) ? "mod" : ""}`}
              style={{ borderLeftColor: CAT_COLOR[cat] ?? CAT_COLOR.UNK }}>
              <div className="tafp-head" onClick={() => setExpanded(expanded === i ? null : i)}>
                <span className="g">{p.group}</span>
                {isNow && <span className="nowtag">now</span>}
                <span className="when">{p.when}</span>
              </div>
              <SummaryChips s={p.summary} groups={p.decoded} />
              {expanded === i && <Groups groups={p.decoded} />}
            </div>
          );
        })}
      </div>
      {decoded.not_decoded && decoded.not_decoded.length > 0 && (
        <div className="dim small">Not decoded: {decoded.not_decoded.join(" ")}</div>
      )}
    </div>
  );
}

export function PirepCard({ raw, decoded, time }: { raw: string; decoded: PirepDecoded; time?: string | null }) {
  return (
    <div className="wxcard">
      <div className="wxhead">
        <b>{raw.includes(" UUA") ? "Urgent PIREP" : "PIREP"}</b>
        {time && <span className="dim when">{fmtTime(time)}</span>}
      </div>
      <code className="wxraw">{raw}</code>
      <Groups groups={decoded.decoded} extra={decoded.not_decoded} />
      {decoded.note && <div className="dim small">{decoded.note}</div>}
    </div>
  );
}

export function ReportList({ reports }: { reports: Report[] }) {
  const [q, setQ] = useState("");
  const shown = reports.filter((r) => !q || (r.station ?? "").includes(q.toUpperCase()) || r.raw.includes(q.toUpperCase()));
  return (
    <section className="reports">
      <div className="reports-head">
        <b>{reports.length} decoded report{reports.length === 1 ? "" : "s"}</b>
        {reports.length > 8 && <input placeholder="Filter station…" value={q} onChange={(e) => setQ(e.target.value)} />}
      </div>
      {shown.map((r, i) => (
        <div key={i} className="report">
          <div className="report-st">{r.station ?? "—"}</div>
          {r.kind === "TAF" && r.decoded ? (
            <TafTimeline raw={r.raw} decoded={r.decoded as TafDecoded} current={false} />
          ) : r.kind === "PIREP" && r.decoded ? (
            <PirepCard raw={r.raw} decoded={r.decoded as PirepDecoded} time={r.time} />
          ) : (
            <MetarCard raw={r.raw} decoded={r.decoded as MetarDecoded | null} time={r.time} compact />
          )}
        </div>
      ))}
    </section>
  );
}
