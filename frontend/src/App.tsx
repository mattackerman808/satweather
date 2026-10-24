import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router";
import StatusBadge from "./StatusBadge";
import LiveView from "./views/LiveView";
import TextView from "./views/TextView";

// MapLibre and OpenSeadragon are most of the bundle; only the pages that
// use them should pay for them.
const MapView = lazy(() => import("./views/MapView"));
const LoopView = lazy(() => import("./views/LoopView"));
const TropicalView = lazy(() => import("./views/TropicalView"));

const NAV = [
  { to: "/", label: "Live" },
  { to: "/satellite", label: "Satellite" },
  { to: "/radar", label: "Radar & charts" },
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
          {/* key: a fresh instance per group, not one reused across the two routes */}
          <Route path="/satellite" element={<Suspense fallback={null}><LoopView key="satellite" group="satellite" /></Suspense>} />
          <Route path="/radar" element={<Suspense fallback={null}><LoopView key="nws" group="nws" /></Suspense>} />
          <Route path="/map" element={<Suspense fallback={null}><MapView /></Suspense>} />
          <Route path="/text" element={<TextView />} />
          <Route path="/text/:id" element={<TextView />} />
          <Route path="/tropical" element={<Suspense fallback={null}><TropicalView /></Suspense>} />
        </Routes>
      </main>
    </div>
  );
}
