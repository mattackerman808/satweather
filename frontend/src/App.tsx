import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router";
import Placeholder from "./views/Placeholder";
import StatusBadge from "./StatusBadge";

// MapLibre is most of the bundle; only the map pages should pay for it.
const MapView = lazy(() => import("./views/MapView"));

const NAV = [
  { to: "/", label: "Live" },
  { to: "/satellite", label: "Satellite" },
  { to: "/map", label: "Map" },
  { to: "/text", label: "Text" },
  { to: "/tropical", label: "Tropical & Meso" },
];

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">satweather</span>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <StatusBadge />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Placeholder title="Live" note="Newest imagery, radar and warnings land here (phase 3)." />} />
          <Route path="/satellite" element={<Placeholder title="Satellite" note="Full Disk, Meso 1/2 and L2 viewer with loops (phase 3)." />} />
          <Route path="/map" element={<Suspense fallback={null}><MapView /></Suspense>} />
          <Route path="/text" element={<Placeholder title="Text" note="Decoded EMWIN products and search (phase 2)." />} />
          <Route path="/tropical" element={<Placeholder title="Tropical & Mesoscale" note="NHC advisories, SPC discussions, Meso sectors (phase 5)." />} />
        </Routes>
      </main>
    </div>
  );
}
