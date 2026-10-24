import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type Frame, type Storm } from "../api";
import "./landing.css";

// The front page: what this is, how the data gets here, and where to go.
// The hero's live line and the full-disk picture are real, from the API.

type Health = { feed?: { ok: boolean; newest_age_s?: number } };

const STAGES: { name: string; body: string; detail: string }[] = [
  {
    name: "GOES-18",
    body: "NOAA's western weather satellite, parked 35,786 km above the equator at 137.2° W. Besides taking "
      + "pictures, it rebroadcasts finished products to anyone listening: its own imagery, the National Weather "
      + "Service's text feed (EMWIN), and relayed images from GOES-19 and Japan's Himawari.",
    detail: "HRIT downlink at 1694.1 MHz",
  },
  {
    name: "The dish on the roof",
    body: "A small dish aimed at the satellite, with a low-noise amplifier at its focus. The amplifier is powered "
      + "over the same cable that carries the signal down into the house.",
    detail: "927,000 symbols a second, BPSK",
  },
  {
    name: "A Raspberry Pi with a radio",
    body: "A Raspberry Pi 4 with a USB software-defined radio runs SatDump, which locks onto the signal, "
      + "demodulates it and decodes it into image files and text bulletins as they come in.",
    detail: "SatDump, decoding live",
  },
  {
    name: "The NAS",
    body: "SatDump writes straight to the network storage in the house. Originals are kept for two weeks.",
    detail: "About 13 GB a day",
  },
  {
    name: "satweather",
    body: "A small server picks up each file as it lands. It reads around 1,200 weather service bulletins an "
      + "hour (warnings, forecasts, airport observations, pilot reports, hurricane advisories) and makes "
      + "web-sized copies of every image.",
    detail: "Parsed, decoded and mapped",
  },
  {
    name: "This site",
    body: "Pages update as new files arrive, usually within a minute of a file finishing on the dish.",
    detail: "You are here",
  },
];

const TABS: { to: string; name: string; body: string }[] = [
  { to: "/live", name: "Live", body: "The newest satellite, radar and hazard images, warnings in effect, and bulletins as they arrive." },
  { to: "/satellite", name: "Satellite", body: "Loops of GOES-18's full disk and mesoscale sectors, GOES-19, Himawari and derived products, with zoom to full resolution." },
  { to: "/radar", name: "Radar & charts", body: "Weather service radar mosaics by region and forecast charts, looped." },
  { to: "/map", name: "Map", body: "Warnings, discussions, airport weather and pilot reports. Click anywhere for that spot's forecast." },
  { to: "/tropical", name: "Tropical & Meso", body: "Active hurricanes with the hurricane center's track and wind radii, outlooks, and where the mesoscale sectors are pointed." },
  { to: "/text", name: "Text", body: "Every bulletin received, searchable." },
];

function since(s?: number): string {
  if (s === undefined) return "no files yet";
  if (s < 90) return `${s} seconds ago`;
  if (s < 5400) return `${Math.round(s / 60)} minutes ago`;
  return `${Math.round(s / 3600)} hours ago`;
}

export default function LandingView() {
  const [health, setHealth] = useState<Health | null>(null);
  const [perHour, setPerHour] = useState<number | null>(null);
  const [storms, setStorms] = useState<Storm[]>([]);
  const [disk, setDisk] = useState<Frame | null>(null);

  useEffect(() => {
    const load = () => {
      fetch("/api/health").then((r) => r.json()).then(setHealth).catch(() => setHealth(null));
      api.types(1).then((t) => setPerHour(t.reduce((n, x) => n + x.n, 0))).catch(() => {});
    };
    load();
    api.tropical().then((d) => setStorms(d.storms.filter((s) => !s.final))).catch(() => {});
    api.frames({ source: "GOES-18", sector: "Full Disk", product: "Clean_Longwave_IR_Window_Band", overlay: false,
      hours: 6 }, 1).then((f) => setDisk(f.at(-1) ?? null)).catch(() => {});
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, []);

  const receiving = health?.feed?.ok;
  return (
    <div className="landing">
      <header className="hero">
        <picture>
          <source type="image/webp" srcSet="/landing/hero-900.webp 900w, /landing/hero-1448.webp 1448w"
            sizes="100vw" />
          <img src="/landing/hero-1448.webp" alt="A weather satellite beaming its signal down to a dish on a house roof under the Milky Way" />
        </picture>
        <div className="hero-text">
          <h1>GOES-18 weather, received on our roof</h1>
          <p className="lede">Every image and bulletin here came down from a satellite 35,786 km above the
            Pacific, through the dish on the roof, minutes ago. No weather service website in between.</p>
          <div className="hero-actions">
            <Link className="btn primary" to="/live">See what's live</Link>
            <Link className="btn" to="/satellite">Watch the satellite loops</Link>
          </div>
        </div>
        <div className="now" aria-live="polite">
          <span className={`rx-state${receiving ? " on" : ""}`}>
            {health === null ? "Checking the dish" : receiving ? "Receiving" : "Not receiving"}
          </span>
          <span>Last file {since(health?.feed?.newest_age_s)}</span>
          {perHour !== null && <span>{perHour.toLocaleString()} bulletins in the past hour</span>}
          {storms.map((s) => (
            <Link key={s.storm_id} to={`/tropical?storm=${s.storm_id}`}>
              {s.kind} {s.name}{s.category.length === 1 ? `, Category ${s.category}` : ""}
            </Link>
          ))}
        </div>
      </header>

      <section className="path">
        <h2>How it gets here</h2>
        <ol className="stages">
          {STAGES.map((s) => (
            <li key={s.name}>
              <h3>{s.name}</h3>
              <p>{s.body}</p>
              <p className="detail">{s.detail}</p>
            </li>
          ))}
        </ol>
        <p className="aside">The weather all comes from the dish. The internet supplies only the base map
          (OpenStreetMap data, via Protomaps) and the positions of the navigation aids used to place pilot reports.</p>
      </section>

      <section className="tabs-intro">
        <div>
          <h2>What's here</h2>
          <dl>
            {TABS.map((t) => (
              <div key={t.to}>
                <dt><Link to={t.to}>{t.name}</Link></dt>
                <dd>{t.body}</dd>
              </div>
            ))}
          </dl>
        </div>
        {disk && (
          <Link className="disk" to="/satellite?src=GOES-18&sector=Full+Disk">
            <img src={disk.thumb} alt="The latest GOES-18 full-disk infrared image" />
            <span>The latest full disk, in infrared</span>
          </Link>
        )}
      </section>

      <footer className="credits">
        Built on <a href="https://www.satdump.org/">SatDump</a>, data from NOAA and the National Weather Service,
        bulletins parsed with <a href="https://github.com/akrherz/pyIEM">pyIEM</a>, maps by{" "}
        <a href="https://protomaps.com/">Protomaps</a> and <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>{" "}
        contributors, drawn with <a href="https://maplibre.org/">MapLibre</a> and{" "}
        <a href="https://openseadragon.github.io/">OpenSeadragon</a>.
      </footer>
    </div>
  );
}
