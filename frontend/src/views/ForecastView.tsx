import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ago, api, fmtTime, type Forecast, type LlmResult, type OutlookText } from "../api";
import { useLiveEvents } from "../events";
import PlaceSearch from "../map/PlaceSearch";
import AfdText from "../wx/AfdText";
import { DayStrip } from "../wx/DayStrip";
import ForecastCharts from "../wx/ForecastCharts";
import { ZoneForecastText } from "../wx/ZonePanel";

// A 7-10 day forecast for a place: the nearest NWS point forecast (PFM) as
// day panels and charts, the zone forecast, hazards in effect, what changed
// since the previous forecast, the local forecasters' discussion, and the
// national extended outlooks; plus, when white's LLM is up, a plain-English
// outlook written from those products (days 8-10 from CPC). All of it
// arrived over GOES-18's HRIT downlink.

const KEY = "satweather.forecast.at";
const NATIONAL: [string, string][] = [["PMDEPD", "Extended forecast discussion (WPC, days 3-7)"],
  ["PMDMRD", "6-10 and 8-14 day outlook discussion (CPC)"], ["PMDTHR", "U.S. hazards outlook (CPC, days 3-14)"]];

function readAt(s: string | null): [number, number] | null {
  const m = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(s ?? "");
  if (!m) return null;
  const lon = Number(m[1]), lat = Number(m[2]);
  return Math.abs(lon) <= 180 && Math.abs(lat) <= 90 ? [lon, lat] : null;
}

function stored(): [number, number] | null {
  try { return readAt(localStorage.getItem(KEY)); } catch { return null; }
}

export default function ForecastView() {
  const [params, setParams] = useSearchParams();
  const at = readAt(params.get("at")) ?? stored();
  const [f, setF] = useState<Forecast | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const choose = useCallback((lon: number, lat: number) => {
    const v = `${lon.toFixed(4)},${lat.toFixed(4)}`;
    try { localStorage.setItem(KEY, v); } catch { /* private window */ }
    setParams({ at: v }, { replace: true });
  }, [setParams]);

  const key = at ? `${at[0]},${at[1]}` : "";
  useEffect(() => {
    if (!at) return;
    let stale = false;
    setErr(null);
    api.forecast(at[0], at[1]).then((x) => !stale && setF(x)).catch((e) => !stale && setErr(String(e)));
    return () => { stale = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);
  useEffect(() => { setF(null); }, [key]);

  // A new forecast for this office (or a new national discussion) refreshes the page.
  const office = f?.zone?.cwa ?? f?.point?.office?.slice(-3);
  const pending = useRef<ReturnType<typeof setTimeout>>(undefined);
  useLiveEvents((e) => {
    if (e.type !== "product" || !f) return;
    const mine = ["PFM", "ZFP", "AFD"].includes(e.nnn) && office && e.office?.endsWith(office);
    if (mine || ["PMDEPD", "PMDMRD", "PMDTHR"].includes(e.awips)) {
      clearTimeout(pending.current);
      pending.current = setTimeout(() => setTick((t) => t + 1), 5000);
    }
  });

  return (
    <div className="forecastview">
      <div className="fcbar">
        <h1>Forecast</h1>
        <PlaceSearch near={() => at ?? undefined} onPick={choose} className="wide" />
      </div>
      {!at && (
        <div className="fcempty">
          <p>Search for a city ("Davis, CA"), a ZIP code, a forecast zone or an airport, or use <b>⌖</b> for where you are.</p>
          <p className="dim">The 7-day numbers are the National Weather Service's own point forecasts, as received from
            GOES-18; days 8-10 come from the Climate Prediction Center's outlook.</p>
        </div>
      )}
      {at && err && <p className="empty">{err}</p>}
      {at && !f && !err && <p className="empty">Loading…</p>}
      {f && <Body f={f} />}
    </div>
  );
}

function Body({ f }: { f: Forecast }) {
  const p = f.point;
  const z = f.zone;
  const [outlook, setOutlook] = useState<LlmResult<OutlookText> | null>(null);
  const [afdRaw, setAfdRaw] = useState<string | null>(null);

  useEffect(() => {
    setOutlook(null);
    if (!f.llm.enabled || (!p && !f.forecast)) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (tries: number) => {
      api.outlook(f.lon, f.lat).then((r) => {
        if (stale) return;
        setOutlook(r);
        if (r.status === "pending" && tries < 20) timer = setTimeout(() => load(tries + 1), 4000);
      }).catch(() => !stale && setOutlook({ status: "unavailable", content: null }));
    };
    setOutlook({ status: "pending", content: null });
    load(0);
    return () => { stale = true; clearTimeout(timer); };
  }, [f, p]);

  useEffect(() => {
    setAfdRaw(null);
    if (!f.afd) return;
    let stale = false;
    api.product(f.afd.id).then((d) => !stale && setAfdRaw(d.raw)).catch(() => {});
    return () => { stale = true; };
  }, [f.afd]);

  const title = z ? `${z.name}${z.state ? `, ${z.state}` : ""}` : p?.name ?? "This spot";

  return (
    <>
      <header className="fchead">
        <h2>{title}</h2>
        <div className="kicker">
          {p && <span>Forecast point: {p.name.replace(/-[^-]*$/, "")}{p.distance_km >= 1 ? `, ${Math.round(p.distance_km)} km away` : ""}</span>}
          {(z?.cwa || p?.office) && <span>NWS {z?.cwa ?? p?.office.slice(-3)}</span>}
          {p && <span>issued {ago(p.issued)}</span>}
        </div>
      </header>

      {f.hazards.length > 0 && (
        <section className="fchazards">
          {f.hazards.map((h) => (
            <Link key={`${h.office}.${h.phenomena}.${h.significance}.${h.etn}`} className="pick" to={`/text/${h.product_id}`}>
              <span className="sw" style={{ background: h.color }} /> {h.name}
              <span className="dim">{h.ends ? `until ${fmtTime(h.ends)}` : ""}</span>
            </Link>
          ))}
        </section>
      )}

      {p && f.daily.length > 0 ? (
        <>
          <DayStrip f={f} />
          <ForecastCharts f={f} />
        </>
      ) : (
        <p className="empty">No NWS point forecast within 100 km of here; the zone forecast below is the forecast.</p>
      )}

      {f.changes.length > 0 && (
        <section className="card fcchanges">
          <h3>What changed since the previous forecast <span className="dim">({ago(p?.prev_issued)})</span></h3>
          <ul>{f.changes.map((c, i) => <li key={i}><b>{weekdayOf(c.date)}:</b> {c.text}</li>)}</ul>
        </section>
      )}

      {/* The text: the two AI summaries first (the outlook, then the
          forecasters' discussion in plain English), then the zone forecast. */}
      <div className="fctext">
        {outlook && outlook.status !== "unavailable" && outlook.status !== "none" && <OutlookCard r={outlook} />}
        {f.afd && (
          <section className="card">
            <h3>Forecasters' discussion <span className="dim">{f.afd.awips} · {ago(f.afd.issued ?? f.afd.received)}</span></h3>
            {afdRaw ? <AfdText id={f.afd.id} raw={afdRaw} start={f.llm.enabled ? "plain" : "raw"} /> : <p className="dim">Loading…</p>}
          </section>
        )}
        {f.forecast && (
          <section className="card">
            <h3>{f.forecast.title} <span className="dim">{f.forecast.awips} · {ago(f.forecast.issued ?? f.forecast.received)}</span></h3>
            <ZoneForecastText f={f.forecast} />
            <Link to={`/text/${f.forecast.product_id}`}>Whole product →</Link>
          </section>
        )}
      </div>

      <section className="card fcnational">
        <h3>National outlooks</h3>
        {NATIONAL.map(([a, label]) => {
          const n = f.national[a];
          return n && (
            <Link key={a} className="pick" to={`/text/${n.id}`}>{label} <span className="dim">{ago(n.issued ?? n.received)}</span></Link>
          );
        })}
      </section>
    </>
  );
}

const weekdayOf = (d: string) => new Date(`${d}T12:00:00Z`).toLocaleDateString([], { weekday: "long", timeZone: "UTC" });

// "[PFM]" / "[AFD, WPC]" source tags at sentence ends become small chips.
const TAG = /\s*\[((?:PFM|ZFP|AFD|WPC|CPC|CHG)(?:\s*[,/;]\s*(?:PFM|ZFP|AFD|WPC|CPC|CHG))*)\]/g;
const TAG_NAME: Record<string, string> = { PFM: "Point forecast", ZFP: "Zone forecast", AFD: "Forecasters' discussion",
  WPC: "WPC extended discussion", CPC: "CPC 6-10/8-14 day outlook", CHG: "Changes since the last forecast" };

function Tagged({ text }: { text: string }) {
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TAG)) {
    out.push(text.slice(last, m.index));
    out.push(...m[1].split(/\s*[,/;]\s*/).map((t, i) => (
      <span key={`${m.index}-${i}`} className="srctag" title={TAG_NAME[t]}>{t}</span>
    )));
    last = m.index! + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

function OutlookCard({ r }: { r: LlmResult<OutlookText> }) {
  if (r.status === "pending" || !r.content) {
    if (r.status === "failed") return null;
    return (
      <section className="card outlookcard">
        <p className="dim aiwait"><span className="spinner" aria-hidden /> Writing the outlook from the latest NWS
          products…</p>
      </section>
    );
  }
  const c = r.content;
  return (
    <section className="card outlookcard">
      <div className="ainote">AI summary of the NWS products below{r.created ? `, written ${ago(r.created)}` : ""}
        {r.stale ? " (the writer is offline; this is the last one)" : ""}. The forecasts themselves are the authority.</div>
      <h3 className="outlookhead"><Tagged text={c.headline} /></h3>
      <p><Tagged text={c.summary} /></p>
      {c.trends?.length > 0 && <ul className="trends">{c.trends.map((t, i) => <li key={i}><Tagged text={t} /></li>)}</ul>}
      {c.changes?.length > 0 && (
        <p className="changes"><b>Since the last forecast: </b>{c.changes.map((t, i) => <span key={i}><Tagged text={t} /> </span>)}</p>
      )}
      {typeof c.beyond_day7 === "string" && c.beyond_day7 && <p className="beyond"><b>Days 8-10: </b><Tagged text={c.beyond_day7} /></p>}
      {c.confidence && <p className="dim"><Tagged text={c.confidence} /></p>}
    </section>
  );
}
