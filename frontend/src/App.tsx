import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router";
import Placeholder from "./views/Placeholder";
import StatusBadge from "./StatusBadge";
import LiveView from "./views/LiveView";
import TextView from "./views/TextView";

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
          <Route path="/" element={<LiveView />} />
          <Route path="/satellite" element={<Placeholder title="Satellite" note="Full Disk, Meso 1/2 and L2 viewer with loops (phase 3)." />} />
          <Route path="/map" element={<Suspense fallback={null}><MapView /></Suspense>} />
          <Route path="/text" element={<TextView />} />
          <Route path="/text/:id" element={<TextView />} />
          <Route path="/tropical" element={<Placeholder title="Tropical & Mesoscale" note="NHC advisories, SPC discussions, Meso sectors (phase 5)." />} />
        </Routes>
      </main>
    </div>
  );
}
