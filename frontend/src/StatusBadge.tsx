import { useEffect, useState } from "react";

type Health = {
  status: "ok" | "degraded";
  db: { ok: boolean };
  feed: { ok: boolean; newest_age_s?: number };
};

// The hashed entry script this page is running. A deploy (`make push` or
// a new deb) changes the name in index.html, and a tab left open would go
// on calling the new API with the old code; offer a reload instead.
const ENTRY = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.src;

async function outdated(): Promise<boolean> {
  if (!ENTRY) return false; // vite dev server: no hashed bundle
  const html = await (await fetch("/index.html", { cache: "no-cache" })).text();
  const m = html.match(/src="([^"]*\/assets\/[^"]+\.js)"/);
  return !!m && new URL(m[1], location.origin).href !== ENTRY;
}

function age(s?: number) {
  if (s === undefined) return "no data";
  if (s < 120) return `${s}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

// Polls /api/health so a stalled dish or decoder on rtl is visible from
// every page, not only from Grafana; and turns into a reload button once
// a newer build is deployed.
export default function StatusBadge() {
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState(false);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    const check = () => outdated().then(setStale).catch(() => {});
    const t = setInterval(check, 60_000);
    return () => clearInterval(t);
  }, []);

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

  if (stale) {
    return (
      <button className="badge upd" onClick={() => location.reload()} title="A newer build has been deployed">
        Updated — reload
      </button>
    );
  }
  if (err || !h) return <span className="badge bad">{err ? "API down" : "…"}</span>;
  const cls = h.status === "ok" ? "ok" : "bad";
  return (
    <span className={`badge ${cls}`} title={`db ${h.db.ok ? "ok" : "down"}`}>
      feed {age(h.feed.newest_age_s)}
    </span>
  );
}
