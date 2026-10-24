import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api, fmtTime, type ProductDetail as PD } from "../api";
import { ReportList } from "../wx/Wx";
import { reflow } from "../wx/ZonePanel";

const ACTION: Record<string, string> = {
  NEW: "Issued", CON: "Continued", EXT: "Extended", EXA: "Extended (area)", EXB: "Extended (area+time)",
  UPG: "Upgraded", CAN: "Cancelled", EXP: "Expired", COR: "Corrected", ROU: "Routine",
};

// `embedded`: shown inside the map's inspector, where links must not
// navigate away from the map.
export default function ProductDetail({ id, embedded = false, onShowOnMap }:
  { id: number; embedded?: boolean; onShowOnMap?: () => void }) {
  const [p, setP] = useState<PD | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Segments opened to show their own text, and the zone each was opened
  // from (a zone forecast has one segment per group of zones).
  const [open, setOpen] = useState<Map<number, string | null>>(new Map());
  const segRefs = useRef(new Map<number, HTMLElement>());

  useEffect(() => {
    setP(null);
    setErr(null);
    setOpen(new Map());
    api.product(id).then(setP).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <p className="empty">{err}</p>;
  if (!p) return <p className="empty">Loading…</p>;

  const decoded = p.segments.some((s) => s.ugcs.length || s.headlines.length) || p.vtec.length > 0;
  const toggle = (id: number, ugc: string | null, force?: boolean) => {
    setOpen((o) => {
      const next = new Map(o);
      if (force || !next.has(id) || (ugc && next.get(id) !== ugc)) next.set(id, ugc);
      else next.delete(id);
      return next;
    });
    requestAnimationFrame(() => segRefs.current.get(id)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };

  return (
    <article className="product">
      <header>
        <div className="kicker">
          <span className={`catbadge cat-${p.category}`}>{p.type_name}</span>
          <span>{p.awips}</span>
          <span>{p.office}</span>
          <span>{p.wmo} {p.cccc}</span>
          {p.priority === 1 && <span className="urgent">priority</span>}
        </div>
        <h2>{p.title}</h2>
        <div className="times">
          Issued {fmtTime(p.issued)} · received {fmtTime(p.received)}
        </div>
      </header>

      {p.vtec.length > 0 && (
        <div className="vteclist">
          {p.vtec.map((v, i) => (
            <div key={i} className="vtec" style={{ borderLeftColor: v.color }}>
              <b>{v.name}</b> #{v.etn} · {ACTION[v.action] ?? v.action}
              <span className="dim"> · until {fmtTime(v.ends)}</span>
            </div>
          ))}
        </div>
      )}

      {decoded &&
        p.segments
          .filter((s) => s.ugcs.length || s.headlines.length || s.polygon)
          .map((s) => (
            <section key={s.id} className={`segment${open.has(s.id) ? " open" : ""}`}
              ref={(el) => { if (el) segRefs.current.set(s.id, el); else segRefs.current.delete(s.id); }}>
              {s.headlines.map((h) => (
                <div key={h} className="headline">{h}</div>
              ))}
              {s.places.length > 0 && (
                <div className="places">
                  {s.places.map((z) => (
                    <button key={z.ugc} className="linkbtn" title={`${z.ugc}: show this forecast`}
                      onClick={() => toggle(s.id, z.ugc, true)}>{z.name}</button>
                  ))}
                </div>
              )}
              {!s.places.length && s.ugcs.length > 0 && <div className="places dim">{s.ugcs.join(" ")}</div>}
              <div className="segmeta">
                {s.ugc_expire && <span>expires {fmtTime(s.ugc_expire)}</span>}
                {s.motion && <span>moving {Math.round(s.motion.dir)}° at {s.motion.kt} kt</span>}
                {s.tags.hail && <span>hail {String(s.tags.hail)} in</span>}
                {s.tags.wind && <span>wind {String(s.tags.wind)} {String(s.tags.windunits ?? "")}</span>}
                {s.tags.tornado && <span>tornado {String(s.tags.tornado)}</span>}
                {s.tags.emergency && <span className="urgent">EMERGENCY</span>}
                {s.tags.pds && <span className="urgent">PDS</span>}
                {s.body.trim() && (
                  <button className="linkbtn" onClick={() => toggle(s.id, null)} aria-expanded={open.has(s.id)}>
                    {open.has(s.id) ? "Hide text" : "Show text"}
                  </button>
                )}
                {s.polygon && (embedded ? (
                  <button className="linkbtn" onClick={onShowOnMap}>zoom to polygon</button>
                ) : (
                  <Link to={`/map?product=${p.id}`}>show on map</Link>
                ))}
              </div>
              {open.has(s.id) && (
                <div className="segtext">
                  <div className="fcst">
                    {reflow(s.body).map((x, i) => x.pre ? <pre key={i} className="raw">{x.text}</pre> : (
                      <p key={i}>{x.label && <b>{x.label}: </b>}{x.text}</p>
                    ))}
                  </div>
                  {!embedded && (() => {
                    const z = s.places.find((x) => x.ugc === open.get(s.id)) ?? s.places[0];
                    return z && <Link to={`/text?ugc=${z.ugc}`}>Other products for {z.name}</Link>;
                  })()}
                </div>
              )}
            </section>
          ))}

      {p.reports.length > 0 && <ReportList reports={p.reports} />}

      {p.parse_error && !p.reports.length && <div className="parsenote">Shown as received: {p.parse_error}</div>}

      <div className="rawbar">
        <span>Full text</span>
        <a href={`/orig/${p.orig_path}`} download>original file</a>
      </div>
      <pre className="raw">{p.raw}</pre>
    </article>
  );
}
