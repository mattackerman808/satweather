import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { ago, api, type Filters, type Page, type ProductSummary, type ProductType } from "../api";
import { useLiveEvents } from "../events";
import ProductDetail from "./ProductDetail";

const CAT_ORDER = ["warning", "watch", "advisory", "tropical", "forecast", "discussion", "observation",
  "aviation", "marine", "hydro", "climate", "other"];

function useFilters(): [Filters, (f: Filters) => void] {
  const [sp, setSp] = useSearchParams();
  const f = useMemo<Filters>(
    () => ({
      q: sp.get("q") ?? undefined,
      category: sp.getAll("category"),
      nnn: sp.getAll("nnn"),
      office: sp.get("office") ?? undefined,
      ugc: sp.get("ugc") ?? undefined,
    }),
    [sp],
  );
  const set = (n: Filters) => {
    const p = new URLSearchParams();
    if (n.q) p.set("q", n.q);
    n.category?.forEach((c) => p.append("category", c));
    n.nnn?.forEach((c) => p.append("nnn", c));
    if (n.office) p.set("office", n.office);
    if (n.ugc) p.set("ugc", n.ugc);
    setSp(p, { replace: true });
  };
  return [f, set];
}

function matches(f: Filters, e: { category: string; nnn: string; office: string }) {
  if (f.q || f.ugc) return false; // server-side only; show the "new" pill instead
  if (f.category?.length && !f.category.includes(e.category)) return false;
  if (f.nnn?.length && !f.nnn.includes(e.nnn)) return false;
  if (f.office && f.office.toUpperCase() !== e.office) return false;
  return true;
}

export default function TextView() {
  const { id } = useParams();
  const nav = useNavigate();
  const [f, setF] = useFilters();
  const [cats, setCats] = useState<Record<string, string>>({});
  const [types, setTypes] = useState<ProductType[]>([]);
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [next, setNext] = useState<Page["next"]>(null);
  const [loading, setLoading] = useState(false);
  const [fresh, setFresh] = useState(0);
  const [q, setQ] = useState(f.q ?? "");
  const key = JSON.stringify(f);

  useEffect(() => {
    api.categories().then(setCats).catch(() => {});
    api.types().then(setTypes).catch(() => {});
  }, []);

  const load = useCallback(
    (cursor?: Page["next"]) => {
      setLoading(true);
      api
        .products(f, cursor)
        .then((p) => {
          setItems((old) => (cursor ? [...old, ...p.items] : p.items));
          setNext(p.next);
          if (!cursor) setFresh(0);
        })
        .finally(() => setLoading(false));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  useEffect(() => load(), [load]);

  useLiveEvents((e) => {
    if (e.type !== "product") return;
    if (matches(f, e)) {
      const s: ProductSummary = {
        id: e.id, received: e.received, issued: e.issued, awips: e.awips, nnn: e.nnn, office: e.office,
        title: e.title, category: e.category, priority: e.priority, raw_only: false, has_vtec: e.vtec, snippet: null,
      };
      setItems((old) => [s, ...old].slice(0, 500));
    } else if (f.q || f.ugc) {
      setFresh((n) => n + 1);
    }
  });

  const byCat = useMemo(() => {
    const m: Record<string, ProductType[]> = {};
    types.forEach((t) => (m[t.category] ??= []).push(t));
    return m;
  }, [types]);

  const toggle = (list: string[] | undefined, v: string) =>
    list?.includes(v) ? list.filter((x) => x !== v) : [...(list ?? []), v];

  return (
    <div className="textview">
      <aside className="filters">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setF({ ...f, q: q || undefined });
          }}
        >
          <input className="search" placeholder="Search text… (e.g. hail, KTBW, “red flag”)" value={q}
            onChange={(e) => setQ(e.target.value)} />
        </form>
        {(f.q || f.category?.length || f.nnn?.length || f.office || f.ugc) && (
          <button className="clear" onClick={() => { setQ(""); setF({}); }}>Clear filters</button>
        )}
        {f.ugc && <div className="chip on">zone {f.ugc}</div>}
        {CAT_ORDER.filter((c) => byCat[c]).map((c) => (
          <div key={c} className="catgroup">
            <button className={`cat ${f.category?.includes(c) ? "on" : ""}`}
              onClick={() => setF({ ...f, category: toggle(f.category, c) })}>
              {cats[c] ?? c}
              <span className="n">{byCat[c].reduce((a, t) => a + t.n, 0)}</span>
            </button>
            <div className="types">
              {byCat[c].map((t) => (
                <button key={t.nnn} title={t.name} className={`type ${f.nnn?.includes(t.nnn) ? "on" : ""}`}
                  onClick={() => setF({ ...f, nnn: toggle(f.nnn, t.nnn) })}>
                  <b>{t.nnn}</b> {t.name} <span className="n">{t.n}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
        <p className="hint">Counts: last 24 h.</p>
      </aside>

      <section className="plist">
        {fresh > 0 && (
          <button className="freshpill" onClick={() => load()}>{fresh} new — refresh</button>
        )}
        {items.map((p) => (
          <button key={p.id} className={`prow cat-${p.category} ${String(p.id) === id ? "sel" : ""}`}
            onClick={() => nav({ pathname: `/text/${p.id}`, search: location.search })}>
            <div className="prow-top">
              <span className="awips">{p.awips}</span>
              <span className="office">{p.office}</span>
              <span className="when" title={p.received}>{ago(p.received)}</span>
            </div>
            <div className="ptitle">{p.title}</div>
            {p.snippet && (
              <div className="snippet"
                dangerouslySetInnerHTML={{ __html: escapeHl(p.snippet) }} />
            )}
          </button>
        ))}
        {!items.length && !loading && <p className="empty">Nothing matches.</p>}
        {next && (
          <button className="more" disabled={loading} onClick={() => load(next)}>
            {loading ? "Loading…" : "Older"}
          </button>
        )}
      </section>

      <section className="pdetail">
        {id ? <ProductDetail id={Number(id)} /> : <p className="empty">Pick a product.</p>}
      </section>
    </div>
  );
}

// ts_headline marks matches with << >>; escape everything else.
function escapeHl(s: string) {
  return s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/&lt;&lt;/g, "<mark>").replace(/&gt;&gt;/g, "</mark>");
}
