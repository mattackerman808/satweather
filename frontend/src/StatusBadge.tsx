import { useEffect, useState } from "react";

type Health = {
  status: "ok" | "degraded";
  db: { ok: boolean };
  feed: { ok: boolean; newest_age_s?: number };
};

function age(s?: number) {
  if (s === undefined) return "no data";
  if (s < 120) return `${s}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

// Polls /api/health so a stalled dish or decoder on rtl is visible from
// every page, not only from Grafana.
export default function StatusBadge() {
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/health")
        .then((r) => r.json())
        .then((j) => alive && (setH(j), setErr(false)))
        .catch(() => alive && setErr(true));
    load();
    const t = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (err || !h) return <span className="badge bad">{err ? "API down" : "…"}</span>;
  const cls = h.status === "ok" ? "ok" : "bad";
  return (
    <span className={`badge ${cls}`} title={`db ${h.db.ok ? "ok" : "down"}`}>
      feed {age(h.feed.newest_age_s)}
    </span>
  );
}
