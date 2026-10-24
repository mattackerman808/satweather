import { useEffect, useRef } from "react";

// One EventSource for the whole app, shared by every subscriber. The
// server sends one message per product/image as it is ingested.
export type LiveEvent =
  | {
      type: "product";
      id: number;
      nnn: string;
      awips: string;
      office: string;
      category: string;
      title: string;
      issued: string | null;
      received: string;
      priority: number;
      vtec: boolean;
    }
  | { type: "image"; source_id: number; awips: string; received: string }
  // A satellite image's web copy is ready (satweather-imaging).
  | { type: "frame"; id: number; satellite: string; sector: string; product: string; kind: string; overlay: boolean;
      scan_time: string; display: string; thumb: string; width: number; height: number };

type Handler = (e: LiveEvent) => void;
const handlers = new Set<Handler>();
let source: EventSource | null = null;

function ensure() {
  if (source) return;
  source = new EventSource("/api/events");
  source.onmessage = (m) => {
    try {
      const e = JSON.parse(m.data) as LiveEvent;
      handlers.forEach((h) => h(e));
    } catch {
      /* keepalives and malformed lines are ignored */
    }
  };
}

export function useLiveEvents(handler: Handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    ensure();
    const h: Handler = (e) => ref.current(e);
    handlers.add(h);
    return () => {
      handlers.delete(h);
    };
  }, []);
}
