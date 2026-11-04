import { useEffect, useRef, useState } from "react";

// Small helpers shared by the hand-rolled SVG charts (Signal, Forecast).

export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export const fmtClock = (t: number, hours: number) => {
  const d = new Date(t);
  return hours > 48
    ? d.toLocaleDateString([], { weekday: "short", day: "numeric" })
    : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};

// Hourly steps, widened until the labels have room (about 64 px each).
export function timeTicks(t0: number, t1: number, hours: number, px: number): number[] {
  const fit = Math.max(2, Math.floor(px / 64));
  const step = ([1, 2, 3, 6, 12, 24, 48].find((h) => hours / h <= fit) ?? 48) * 3600_000;
  const out: number[] = [];
  const start = Math.ceil(t0 / step) * step;
  for (let t = start; t <= t1; t += step) out.push(t);
  return out;
}

export function niceTicks(lo: number, hi: number, n = 4): number[] {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(6));
  return out;
}

// A finger lifting fires pointerleave too; a tapped value stays up until
// the next tap rather than vanishing with the finger.
export const leave = (set: (v: null) => void) => (e: React.PointerEvent) => e.pointerType === "mouse" && set(null);
