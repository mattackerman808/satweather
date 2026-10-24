import { useEffect, useState } from "react";
import { ago, api, fmtTime, type ZoneAt } from "../api";
import type { Inspect } from "../map/Inspector";

// NWS text comes hard-wrapped at 69 columns. Rejoin each paragraph so it
// wraps to the panel, keep forecast periods (".SATURDAY...") as their own
// items, and drop what is shown elsewhere: the segment header (UGC line,
// zone names, issue time) and the "...HEADLINE..." paragraphs. After "&&"
// comes a fixed-width table (city temperatures and rain chances), kept as is.
type Para = { label?: string; text: string; pre?: boolean };
export function reflow(body: string): Para[] {
  let lines = body.trim().replace(/\$\$$/, "").trimEnd().split("\n");
  if (/^[A-Z]{2}[CZ]\d{3}/.test(lines[0] ?? "")) {
    const blank = lines.findIndex((l) => !l.trim());
    lines = blank < 0 ? [] : lines.slice(blank + 1);
  }
  const out: Para[] = [];
  let cur: string[] | null = null;
  const flush = () => {
    if (!cur) return;
    const t = cur.join(" ").replace(/\s+/g, " ").trim();
    cur = null;
    if (!t || /^\.\.\..*\.\.\.$/.test(t)) return;
    const m = /^\.([^.]+?)\.\.\.\s*(.*)$/.exec(t);
    out.push(m ? { label: m[1].charAt(0) + m[1].slice(1).toLowerCase(), text: m[2] } : { text: t });
  };
  for (const [i, l] of lines.entries()) {
    if (l.startsWith("&&")) {
      flush();
      const table = lines.slice(i + 1).join("\n").trim();
      if (table) out.push({ text: table, pre: true });
      break;
    }
    if (!l.trim()) flush();
    else if (/^\s*[.*]/.test(l) && !/^\.\.\./.test(l)) { flush(); cur = [l.trim()]; }
    else (cur ??= []).push(l.trim());
  }
  flush();
  return out;
}

const KIND: Record<string, string> = {
  public: "Public forecast zone", marine: "Marine zone", offshore: "Offshore zone",
};

// What applies where the map was clicked: the zone's latest forecast, the
// hazards in effect at that spot, and other recent products for the zone.
export default function ZonePanel({ lon, lat, onOpen, onZone }: {
  lon: number;
  lat: number;
  onOpen: (i: Inspect) => void;
  onZone: (g: GeoJSON.Geometry | null) => void;
}) {
  const [d, setD] = useState<ZoneAt | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    let stale = false;
    api.zoneAt(lon, lat).then((z) => {
      if (stale) return;
      setD(z);
      onZone(z.zone?.geometry ?? null);
    }).catch((e) => setErr(String(e)));
    return () => { stale = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lon, lat]);

  if (err) return <p className="empty">{err}</p>;
  if (!d) return <p className="empty">Loading…</p>;
  const z = d.zone;
  const f = d.forecast;
  const expired = f?.ugc_expire && new Date(f.ugc_expire).getTime() < Date.now();
  return (
    <article className="zonepanel">
      <header>
        <div className="kicker">
          {z && <span>{z.ugc}</span>}
          {z && <span>{KIND[z.kind] ?? z.kind}</span>}
          {z?.cwa && <span>NWS {z.cwa}</span>}
          <span>{lat.toFixed(3)}, {lon.toFixed(3)}</span>
        </div>
        <h2>{z ? z.name : "No forecast zone here"}</h2>
        {!z && <p className="dim">Open ocean, or outside NWS forecast zones.</p>}
      </header>
      {d.hazards.length > 0 && (
        <section>
          <h3>In effect here</h3>
          {d.hazards.map((h) => (
            <button key={`${h.office}.${h.phenomena}.${h.significance}.${h.etn}`} className="pick"
              onClick={() => onOpen({ kind: "product", id: h.product_id })}>
              <span className="sw" style={{ background: h.color }} /> {h.name}
              <span className="dim">{h.ends ? `until ${fmtTime(h.ends)}` : ""}</span>
            </button>
          ))}
        </section>
      )}
      {z && (f ? (
        <section>
          <h3>
            {f.title} <span className="dim">{f.awips} · {ago(f.issued ?? f.received)}</span>
          </h3>
          {expired && <p className="parsenote">This forecast has expired and no newer one has been received.</p>}
          {f.headlines.map((h) => <div key={h} className="headline">{h}</div>)}
          <div className="fcst">
            {reflow(f.body).map((p, i) => p.pre ? <pre key={i} className="raw">{p.text}</pre> : (
              <p key={i}>{p.label && <b>{p.label}: </b>}{p.text}</p>
            ))}
          </div>
          <button className="linkbtn" onClick={() => onOpen({ kind: "product", id: f.product_id })}>
            Whole product →
          </button>
        </section>
      ) : (
        <p className="empty">No forecast for this zone received yet.</p>
      ))}
      {d.products.length > 0 && (
        <section>
          <h3>Also for this zone</h3>
          {d.products.map((p) => (
            <button key={p.id} className="pick" onClick={() => onOpen({ kind: "product", id: p.id })}>
              {p.type_name} <span className="dim">{p.awips} · {ago(p.received)}</span>
            </button>
          ))}
        </section>
      )}
    </article>
  );
}
