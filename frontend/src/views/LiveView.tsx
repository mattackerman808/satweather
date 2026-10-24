import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ago, api, type Frame, type ProductSummary } from "../api";
import { useLiveEvents } from "../events";

// The newest image of each view that matters at a glance, then what is in
// effect and the live text feed. Tiles open their loop; all of it updates
// from the event stream.
const LOUD = new Set(["warning", "watch", "tropical"]);

type Tile = { title: string; page: "satellite" | "radar"; source: string; sector: string; product: string;
  overlay: boolean };
const IR = "Clean_Longwave_IR_Window_Band";
const TILES: Tile[] = [
  { title: "GOES-18 Full Disk", page: "satellite", source: "GOES-18", sector: "Full Disk", product: IR, overlay: true },
  { title: "GOES-18 Meso 1", page: "satellite", source: "GOES-18", sector: "Mesoscale 1", product: IR, overlay: true },
  { title: "GOES-18 Meso 2", page: "satellite", source: "GOES-18", sector: "Mesoscale 2", product: IR, overlay: true },
  { title: "Radar · CONUS", page: "radar", source: "NWS", sector: "Radar", product: "RADREFUS", overlay: false },
  { title: "Watches & warnings", page: "radar", source: "NWS", sector: "Charts", product: "IMGWWAUS", overlay: false },
  { title: "Himawari", page: "satellite", source: "Himawari", sector: "Full Disk", product: IR, overlay: true },
  { title: "GOES-19", page: "satellite", source: "GOES-19", sector: "Full Disk", product: IR, overlay: true },
];
const tileKey = (t: { source: string; sector: string; product: string; overlay: boolean }) =>
  `${t.source}|${t.sector}|${t.product}|${t.overlay}`;
const tileLink = (t: Tile) =>
  `/${t.page}?${new URLSearchParams({ src: t.source, sector: t.sector, product: t.product })}`;

export default function LiveView() {
  const [feed, setFeed] = useState<ProductSummary[]>([]);
  const [alerts, setAlerts] = useState<ProductSummary[]>([]);
  const [rate, setRate] = useState<number[]>([]);
  const [latest, setLatest] = useState<Record<string, Frame | null>>({});

  useEffect(() => {
    for (const t of TILES) {
      api.frames({ ...t, hours: 24 * 7 }, 1).then((f) => setLatest((l) => ({ ...l, [tileKey(t)]: f.at(-1) ?? null })))
        .catch(() => setLatest((l) => ({ ...l, [tileKey(t)]: null })));
    }
    api.products({}, undefined, 40).then((p) => setFeed(p.items));
    api.products({ category: ["warning", "watch", "tropical"] }, undefined, 15).then((p) => setAlerts(p.items));
  }, []);

  useLiveEvents((e) => {
    if (e.type === "frame") {
      const k = tileKey(e);
      if (TILES.some((t) => tileKey(t) === k) && e.scan_time > (latest[k]?.scan_time ?? "")) {
        setLatest((l) => ({ ...l, [k]: { id: e.id, scan_time: e.scan_time, display: `/media/${e.display}`,
          thumb: `/media/${e.thumb}`, width: e.width, height: e.height } }));
      }
      return;
    }
    if (e.type !== "product") return;
    const s: ProductSummary = {
      id: e.id, received: e.received, issued: e.issued, awips: e.awips, nnn: e.nnn, office: e.office,
      title: e.title, category: e.category, priority: e.priority, raw_only: false, has_vtec: e.vtec, snippet: null,
    };
    setFeed((f) => [s, ...f].slice(0, 60));
    if (LOUD.has(e.category)) setAlerts((a) => [s, ...a].slice(0, 15));
    setRate((r) => [...r.filter((t) => Date.now() - t < 60_000), Date.now()]);
  });

  return (
    <div className="live">
      <section className="tiles">
        {TILES.map((t) => {
          const f = latest[tileKey(t)];
          return (
            <Link key={tileKey(t)} to={tileLink(t)} className="tile">
              <div className="tileimg">
                {f ? <img src={f.thumb} alt={t.title} /> : <span className="dim">{f === null ? "No images yet" : "…"}</span>}
              </div>
              <div className="tilecap"><b>{t.title}</b><span className="dim">{f ? ago(f.scan_time) : ""}</span></div>
            </Link>
          );
        })}
      </section>
      <div className="livecols">
        <section className="card">
          <h3>Warnings, watches &amp; tropical</h3>
          {alerts.map((p) => (
            <Link key={p.id} to={`/text/${p.id}`} className={`lrow cat-${p.category}`}>
              <span className="awips">{p.awips}</span>
              <span className="ltitle">{p.title}</span>
              <span className="when">{ago(p.received)}</span>
            </Link>
          ))}
          {!alerts.length && <p className="empty">None recently.</p>}
          <Link className="morelink" to="/map">Show on map →</Link>
        </section>
        <section className="card grow">
          <h3>
            Incoming <span className="dim">{rate.length}/min</span>
          </h3>
          <div className="feed">
            {feed.map((p) => (
              <Link key={p.id} to={`/text/${p.id}`} className={`lrow cat-${p.category}`}>
                <span className="awips">{p.awips}</span>
                <span className="office">{p.office}</span>
                <span className="ltitle">{p.title}</span>
                <span className="when">{ago(p.received)}</span>
              </Link>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
