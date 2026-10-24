import { useEffect } from "react";
import { Link } from "react-router";
import ProductDetail from "../views/ProductDetail";
import StationPanel from "../wx/StationPanel";
import ZonePanel from "../wx/ZonePanel";
import { PirepCard } from "../wx/Wx";
import type { PirepDecoded } from "../api";

// The map's right-hand panel. Everything clicked on the map opens here,
// so the map (zoom, layers, position) is never left behind.
// `at`: where on the map a warning was clicked, so its panel can offer the
// zone forecast for that spot too.
type At = { at?: [number, number] };
export type Inspect =
  | ({ kind: "product"; id: number } & At)
  | ({ kind: "products"; items: { id: number; name: string; color: string; office: string }[] } & At)
  | { kind: "zone"; lon: number; lat: number }
  | { kind: "station"; id: string }
  | { kind: "pirep"; raw: string; decoded: PirepDecoded; time: string | null };

export default function Inspector({ item, onClose, onOpen, onShowProduct, onZone }: {
  item: Inspect;
  onClose: () => void;
  onOpen: (i: Inspect) => void;
  onShowProduct: (id: number) => void;
  onZone: (g: GeoJSON.Geometry | null) => void;
}) {
  const at = (item.kind === "product" || item.kind === "products") ? item.at : undefined;
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  return (
    <aside className="inspector">
      <div className="inspector-bar">
        {item.kind === "product" && <Link to={`/text/${item.id}`}>Open in Text ↗</Link>}
        {at && (
          <button className="linkbtn" onClick={() => onOpen({ kind: "zone", lon: at[0], lat: at[1] })}>
            Forecast here
          </button>
        )}
        <button className="close" onClick={onClose} title="Close (Esc)">×</button>
      </div>
      <div className="inspector-body">
        {item.kind === "product" && <ProductDetail id={item.id} embedded onShowOnMap={() => onShowProduct(item.id)} />}
        {item.kind === "station" && <StationPanel id={item.id} />}
        {item.kind === "zone" && <ZonePanel lon={item.lon} lat={item.lat} onOpen={onOpen} onZone={onZone} />}
        {item.kind === "pirep" && <PirepCard raw={item.raw} decoded={item.decoded} time={item.time} />}
        {item.kind === "products" && (
          <div className="picklist">
            <h3>{item.items.length} products here</h3>
            {item.items.map((p) => (
              <button key={p.id} className="pick" onClick={() => onOpen({ kind: "product", id: p.id, at })}>
                <span className="sw" style={{ background: p.color }} /> {p.name} <span className="dim">{p.office}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
