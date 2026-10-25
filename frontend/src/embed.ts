// Opened inside another site: Tachbase's Weather page frames
// /live?embed=tachbase. The landing page introduces satweather to a
// first-time visitor; inside the host it's a detour, so there "/" and the
// brand go to /live instead. The flag is kept for the tab's session because
// in-app links drop the query string.
//
// The host also passes its theme so the frame looks like part of it:
// bg, panel, line, text, muted, accent, ok, bad as hex without the '#'
// (?embed=tachbase&bg=141820&panel=1c2233&...). They become the CSS
// variables of the same names; styles.css derives the rest under
// :root[data-host], and the basemap goes light when the background is.
// The host's own navigation replaces the top bar there.
const KEY = "satweather.embed";
const THEME_KEY = "satweather.embed.theme";
const VARS = ["bg", "panel", "line", "text", "muted", "accent", "ok", "bad"] as const;
const HEX = /^[0-9a-f]{3}([0-9a-f]{3})?$/i;

type Theme = Partial<Record<(typeof VARS)[number], string>>;

function stored(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // storage blocked: the URL is all we have
  }
}

function detect(): string | null {
  const flag = new URLSearchParams(location.search).get("embed");
  if (flag) store(KEY, flag);
  return flag || stored(KEY);
}

function readTheme(): Theme {
  const q = new URLSearchParams(location.search);
  const fromUrl: Theme = {};
  for (const v of VARS) {
    const x = q.get(v);
    if (x && HEX.test(x)) fromUrl[v] = `#${x}`;
  }
  if (Object.keys(fromUrl).length) {
    store(THEME_KEY, JSON.stringify(fromUrl));
    return fromUrl;
  }
  try {
    const saved = JSON.parse(stored(THEME_KEY) || "{}") as Theme;
    return Object.fromEntries(Object.entries(saved).filter(([k, x]) =>
      (VARS as readonly string[]).includes(k) && /^#[0-9a-f]{3,6}$/i.test(String(x)))) as Theme;
  } catch {
    return {};
  }
}

function luminance(hex: string): number {
  const h = hex.slice(1);
  const full = h.length === 3 ? h.replace(/./g, "$&$&") : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export const embedded = detect();
export const home = embedded ? "/live" : "/";

const theme = embedded ? readTheme() : {};
export const scheme: "dark" | "light" = theme.bg && luminance(theme.bg) > 0.5 ? "light" : "dark";

if (embedded) {
  const root = document.documentElement;
  root.dataset.host = embedded;
  root.dataset.scheme = scheme;
  for (const [k, x] of Object.entries(theme)) root.style.setProperty(`--${k}`, x);
}
