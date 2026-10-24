import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ago, api, type ProductSummary } from "../api";
import { useLiveEvents } from "../events";

// Phase 2: the live text feed and what is in effect. Imagery and radar
// join this page in phase 3.
const LOUD = new Set(["warning", "watch", "tropical"]);

export default function LiveView() {
  const [feed, setFeed] = useState<ProductSummary[]>([]);
  const [alerts, setAlerts] = useState<ProductSummary[]>([]);
  const [rate, setRate] = useState<number[]>([]);

  useEffect(() => {
    api.products({}, undefined, 40).then((p) => setFeed(p.items));
    api.products({ category: ["warning", "watch", "tropical"] }, undefined, 15).then((p) => setAlerts(p.items));
  }, []);

  useLiveEvents((e) => {
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
  );
}
