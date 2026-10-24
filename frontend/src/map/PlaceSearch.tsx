import { useEffect, useRef, useState } from "react";
import { api, type Place } from "../api";

const KIND_ICON: Record<Place["kind"], string> = { station: "✈", zone: "▢", city: "•" };

// "Forecast for…": a place, zone or airport, or where you are. Either opens
// the zone panel there; results are ranked by distance from the map's
// center (`near`).
export default function PlaceSearch({ near, onPick }: {
  near: () => [number, number] | undefined;
  onPick: (lon: number, lat: number) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Place[]>([]);
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(false);
  const [locating, setLocating] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const seq = useRef(0);
  // The picked result's label goes into the box; that is not a new search.
  const picked = useRef<string | null>(null);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2 || q === picked.current) { setHits([]); return; }
    picked.current = null;
    const n = ++seq.current;
    const t = setTimeout(() => {
      api.places(term, near()).then((h) => {
        if (n !== seq.current) return;
        setHits(h);
        setSel(0);
        setOpen(true);
      }).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const pick = (p: Place) => {
    setOpen(false);
    picked.current = p.label;
    setQ(p.label);
    onPick(p.lon, p.lat);
  };

  const locate = () => {
    if (!navigator.geolocation) return setMsg("This browser can't share its location.");
    setLocating(true);
    setMsg(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => { setLocating(false); onPick(pos.coords.longitude, pos.coords.latitude); },
      (e) => { setLocating(false); setMsg(e.code === e.PERMISSION_DENIED ? "Location permission denied." : "Couldn't get your location."); },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 600_000 },
    );
  };

  return (
    <div className="placesearch">
      <div className="psrow">
        <input
          className="search"
          value={q}
          placeholder="Forecast for a city, zone or airport…"
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => hits.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((i) => Math.min(hits.length - 1, i + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((i) => Math.max(0, i - 1)); }
            else if (e.key === "Enter" && hits[sel]) pick(hits[sel]);
            else if (e.key === "Escape") { setOpen(false); (e.target as HTMLInputElement).blur(); }
          }}
        />
        <button className="locate" onClick={locate} disabled={locating} title="Forecast for my location">
          {locating ? "…" : "⌖"}
        </button>
      </div>
      {open && q.trim().length >= 2 && (
        <div className="psresults">
          {hits.map((p, i) => (
            <button key={`${p.kind}|${p.label}|${p.lon}`} className={`psitem${i === sel ? " on" : ""}`}
              onMouseDown={(e) => e.preventDefault()} onClick={() => pick(p)} onMouseEnter={() => setSel(i)}>
              <span className="psicon">{KIND_ICON[p.kind]}</span>
              <span className="pslabel">{p.label}</span>
              <span className="dim">{p.detail}</span>
            </button>
          ))}
          {!hits.length && q !== picked.current && <div className="psempty">No matches.</div>}
        </div>
      )}
      {msg && <div className="psmsg">{msg}</div>}
    </div>
  );
}
