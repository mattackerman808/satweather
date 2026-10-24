import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle } from "../map/basemap";

// GOES-18 sits at 137.0°W: open over the western US and the eastern
// Pacific, the part of its disk this site is about.
const START = { center: [-125, 38] as [number, number], zoom: 3.2 };

export default function MapView() {
  const el = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let map: maplibregl.Map | undefined;
    let cancelled = false;
    basemapStyle()
      .then((style) => {
        if (cancelled || !el.current) return;
        map = new maplibregl.Map({ container: el.current, style, ...START, attributionControl: { compact: true } });
        map.addControl(new maplibregl.NavigationControl(), "top-right");
        map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
      })
      .catch((e) => setErr(String(e)));
    return () => {
      cancelled = true;
      map?.remove();
    };
  }, []);

  return (
    <div className="mapwrap">
      <div ref={el} className="map" />
      {err && <div className="maperr">Basemap unavailable: {err}</div>}
    </div>
  );
}
