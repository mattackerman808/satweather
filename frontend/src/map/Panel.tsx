import { useState, type ReactNode } from "react";

// The right-hand panel over a map. On a phone it is a bottom sheet over
// the lower half of the map, which can be pulled up to fill it.
export default function Panel({ bar, bodyClass = "", onClose, closeTitle = "Close", children }: {
  bar: ReactNode;
  bodyClass?: string;
  onClose: () => void;
  closeTitle?: string;
  children: ReactNode;
}) {
  const [full, setFull] = useState(false);
  return (
    <aside className={`inspector${full ? " full" : ""}`}>
      <div className="inspector-bar">
        <button className="sheetbtn" onClick={() => setFull((f) => !f)} aria-label={full ? "Shrink" : "Expand"}>
          {full ? "▾" : "▴"}
        </button>
        {bar}
        <button className="close" onClick={onClose} title={closeTitle}>×</button>
      </div>
      <div className={`inspector-body ${bodyClass}`}>{children}</div>
    </aside>
  );
}
