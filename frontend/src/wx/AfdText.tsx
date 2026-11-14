import { useEffect, useState } from "react";
import { api, ago, type LlmResult, type PlainAfd } from "../api";

// An Area Forecast Discussion as the forecasters wrote it, or rewritten in
// plain English by the local LLM (white's model, on the LAN; with a glossary
// of the jargon). The rewrite is made on first request (~30 s) and cached per
// product for everyone. While it is being written the forecasters' text is
// one click away, and the Plain English tab shows its progress; once asked
// for, the rewrite keeps loading even while the reader is on the text.
export default function AfdText(props: { id: number; raw: string; start?: "raw" | "plain" }) {
  return <Afd key={props.id} {...props} />;
}

function Afd({ id, raw, start = "raw" }: { id: number; raw: string; start?: "raw" | "plain" }) {
  const [mode, setMode] = useState<"raw" | "plain">(start);
  const [want, setWant] = useState(start === "plain");  // the rewrite has been asked for
  const [plain, setPlain] = useState<LlmResult<PlainAfd> | null>(null);
  const [since, setSince] = useState(() => Date.now());

  useEffect(() => { if (mode === "plain") setWant(true); }, [mode]);

  useEffect(() => {
    if (!want) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setSince(Date.now());
    const load = (tries: number) => {
      api.plain(id).then((r) => {
        if (stale) return;
        setPlain(r);
        // Another request is writing it: look again shortly.
        if (r.status === "pending" && tries < 30) timer = setTimeout(() => load(tries + 1), 4000);
      }).catch(() => !stale && setPlain({ status: "unavailable", content: null }));
    };
    load(0);
    return () => { stale = true; clearTimeout(timer); };
  }, [want, id]);

  const writing = want && (!plain || plain.status === "pending");
  const ready = plain?.status === "ready" && !!plain.content;

  return (
    <div className="afdtext">
      <div className="seg" role="tablist">
        <button role="tab" aria-selected={mode === "raw"} className={mode === "raw" ? "on" : ""}
          onClick={() => setMode("raw")}>Forecasters' text</button>
        <button role="tab" aria-selected={mode === "plain"} className={mode === "plain" ? "on" : ""}
          onClick={() => setMode("plain")}>
          Plain English
          {writing && <span className="spinner sm" aria-label="being written" />}
          {ready && mode === "raw" && <span className="readydot" aria-label="ready" />}
        </button>
      </div>
      {mode === "raw" ? (
        <>
          {writing && <p className="ainote">The plain-English version is still being written by the local AI model;
            the tab will show a dot when it's ready.</p>}
          <pre className="raw">{raw}</pre>
        </>
      ) : <Plain r={plain} since={since} raw={raw} showRaw={() => setMode("raw")} />}
    </div>
  );
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return <>{Math.max(0, Math.round((now - since) / 1000))} s</>;
}

function Plain({ r, since, raw, showRaw }: {
  r: LlmResult<PlainAfd> | null; since: number; raw: string; showRaw: () => void;
}) {
  if (!r || r.status === "pending") {
    return (
      <div className="aiprogress">
        <p className="aiwait"><span className="spinner" aria-hidden />
          <span>Writing a plain-English version with a local AI model, on our own server (nothing is sent to an
            outside service). <b><Elapsed since={since} /></b> so far; it usually takes about half a minute, and is
            then saved for everyone who reads this discussion.</span></p>
        <button className="linkish" onClick={showRaw}>Read the forecasters' text meanwhile →</button>
      </div>
    );
  }
  if (r.status !== "ready" || !r.content) {
    return (
      <>
        <p className="dim">{r.status === "unavailable"
          ? "The local AI model that writes the plain-English version is offline right now."
          : "The local AI model couldn't write a plain-English version of this one."} Here is the forecasters' text,
          which is the full story.</p>
        <pre className="raw">{raw}</pre>
      </>
    );
  }
  const c = r.content;
  return (
    <div className="plainafd">
      <p className="ainote">Rewritten by a local AI model from the forecasters' text{r.created ? `, ${ago(r.created)}` : ""}.
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
