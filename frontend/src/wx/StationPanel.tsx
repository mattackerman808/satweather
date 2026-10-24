import { useEffect, useState } from "react";
import { api, type StationDetail } from "../api";
import { FlightCat, MetarCard, TafTimeline } from "./Wx";

function cToF(c: number | null | undefined) {
  return c === null || c === undefined ? null : Math.round((c * 9) / 5 + 32);
}

export default function StationPanel({ id }: { id: string }) {
  const [d, setD] = useState<StationDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [history, setHistory] = useState(false);

  useEffect(() => {
    setD(null);
    setErr(null);
    api.station(id).then(setD).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <p className="empty">{err}</p>;
  if (!d) return <p className="empty">Loading…</p>;
  const latest = d.metars[0];
  const s = d.station;
  return (
    <article className="station">
      <header>
        <div className="kicker">
          <span>{s.id}</span>
          {s.faa && s.faa !== s.id && <span>{s.faa}</span>}
          {s.elev_m != null && <span>{Math.round(s.elev_m * 3.281).toLocaleString()} ft</span>}
          {[s.state, s.country].filter(Boolean).join(", ") && <span>{[s.state, s.country].filter(Boolean).join(", ")}</span>}
        </div>
        <h2>{s.name ?? s.id}</h2>
        {latest && (
          <div className="obsline">
            <FlightCat cat={latest.flight_category} />
            {latest.temp_c != null && <span className="big">{cToF(latest.temp_c)}°F</span>}
            {latest.dewpoint_c != null && <span className="dim">dew {cToF(latest.dewpoint_c)}°F</span>}
            {latest.wind_kt != null && (
              <span>
                {latest.wind_kt === 0 ? "calm" : `${latest.wind_dir != null ? Math.round(latest.wind_dir) + "°" : "VRB"} ${Math.round(latest.wind_kt)} kt`}
                {latest.gust_kt ? ` G${Math.round(latest.gust_kt)}` : ""}
              </span>
            )}
            {latest.altimeter_in != null && <span className="dim">{latest.altimeter_in.toFixed(2)} inHg</span>}
          </div>
        )}
      </header>

      <h3>Latest observation</h3>
      {latest ? (
        <MetarCard raw={latest.raw} decoded={latest.decoded} time={latest.valid} />
      ) : (
        <p className="dim">No METAR in the last 24 hours.</p>
      )}

      <h3>Forecast (TAF)</h3>
      {d.taf ? (
        <TafTimeline raw={d.taf.raw} decoded={d.taf.decoded} current={d.taf.current} />
      ) : (
        <p className="dim">No TAF received for this station.</p>
      )}

      {d.metars.length > 1 && (
        <>
          <h3>
            <button className="linkbtn" onClick={() => setHistory((h) => !h)}>
              {history ? "Hide" : "Show"} last {d.metars.length - 1} observations
            </button>
          </h3>
          {history && d.metars.slice(1).map((m, i) => (
            <MetarCard key={i} raw={m.raw} decoded={m.decoded} time={m.valid} compact />
          ))}
        </>
      )}
    </article>
  );
}
