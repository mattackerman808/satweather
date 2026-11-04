import { useEffect, useState } from "react";
import { api, ago, type LlmResult, type PlainAfd } from "../api";

// An Area Forecast Discussion as the forecasters wrote it, or rewritten in
// plain English by the local LLM (with a glossary of the jargon). The
// rewrite is made on first request and cached per product; while white's
// model is off, only the original is offered.
export default function AfdText({ id, raw, start = "raw" }: { id: number; raw: string; start?: "raw" | "plain" }) {
  const [mode, setMode] = useState<"raw" | "plain">(start);
  const [plain, setPlain] = useState<LlmResult<PlainAfd> | null>(null);

  useEffect(() => { setPlain(null); setMode(start); }, [id, start]);

  useEffect(() => {
    if (mode !== "plain" || (plain && plain.status !== "pending")) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = (tries: number) => {
      api.plain(id).then((r) => {
        if (stale) return;
        setPlain(r);
        // Another request is writing it: look again shortly.
        if (r.status === "pending" && tries < 20) timer = setTimeout(() => load(tries + 1), 4000);
      }).catch(() => !stale && setPlain({ status: "unavailable", content: null }));
    };
    load(0);
    return () => { stale = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, id]);

  return (
    <div className="afdtext">
      <div className="seg" role="tablist">
        <button className={mode === "raw" ? "on" : ""} onClick={() => setMode("raw")}>Forecasters' text</button>
        <button className={mode === "plain" ? "on" : ""} onClick={() => setMode("plain")}>Plain English</button>
      </div>
      {mode === "raw" ? <pre className="raw">{raw}</pre> : <Plain r={plain} />}
    </div>
  );
}

function Plain({ r }: { r: LlmResult<PlainAfd> | null }) {
  if (!r || r.status === "pending") {
    return <p className="dim aiwait"><span className="spinner" aria-hidden /> Writing a plain-English version (this takes
      about half a minute the first time)…</p>;
  }
  if (r.status === "unavailable") return <p className="dim">The plain-English writer is offline right now; the
    forecasters' text is the full story.</p>;
  if (r.status !== "ready" || !r.content) return <p className="dim">Couldn't write a plain-English version of this
    one; the forecasters' text is the full story.</p>;
  const c = r.content;
  return (
    <div className="plainafd">
      <p className="ainote">Rewritten by AI from the forecasters' text{r.created ? `, ${ago(r.created)}` : ""}.
        The original is the authority.</p>
      {c.sections.map((s, i) => (
        <section key={i}>
          <h4>{s.title}</h4>
          {s.text.split(/\n{2,}/).map((t, j) => <p key={j}>{t}</p>)}
        </section>
      ))}
      {c.glossary?.length > 0 && (
        <details className="glossary">
          <summary>Glossary ({c.glossary.length})</summary>
          <dl>{c.glossary.map((g) => <div key={g.term}><dt>{g.term}</dt><dd>{g.meaning}</dd></div>)}</dl>
        </details>
      )}
    </div>
  );
}
