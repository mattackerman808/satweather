export type ProductSummary = {
  id: number;
  received: string;
  issued: string | null;
  awips: string;
  nnn: string;
  office: string;
  title: string;
  category: string;
  priority: number;
  raw_only: boolean;
  has_vtec: boolean;
  snippet: string | null;
};

export type Page = { items: ProductSummary[]; next: { before: string; before_id: number } | null };

export type Vtec = {
  segment_id: number;
  action: string;
  office: string;
  phenomena: string;
  significance: string;
  etn: number;
  year: number;
  begins: string | null;
  ends: string | null;
  name: string;
  color: string;
};

export type Segment = {
  id: number;
  seq: number;
  ugcs: string[];
  ugc_expire: string | null;
  headlines: string[];
  motion: { wkt: string; dir: number; kt: number } | null;
  tags: Record<string, string | boolean>;
  polygon: GeoJSON.Polygon | null;
  places: { ugc: string; name: string; kind: string }[];
};

export type ProductDetail = ProductSummary & {
  wmo: string;
  cccc: string;
  raw: string;
  parse_error: string | null;
  filename: string;
  orig_path: string;
  type_name: string;
  segments: Segment[];
  vtec: Vtec[];
  metar_count: number;
  reports: Report[];
};

export type Group = { group: string; meaning: string };
export type WxSummary = { ceiling?: string; visibility?: string; flight_category?: string; weather?: string[] };
export type MetarDecoded = { type: string; station?: string; decoded: Group[]; summary: WxSummary;
  remarks?: Group[]; not_decoded?: string[] };
export type TafPeriod = { group: string; when: string; decoded: Group[]; summary: WxSummary };
export type TafDecoded = { type: "TAF"; station?: string; header: Group[]; periods: TafPeriod[];
  not_decoded?: string[]; note?: string;
  at?: { time: string; prevailing: { group: string; when: string; summary: WxSummary } | null;
         also_possible: { group: string; when: string; summary: WxSummary }[] } };
export type PirepDecoded = { type: "PIREP"; station?: string; decoded: Group[]; note?: string; not_decoded?: string[] };

export type Report = { kind: "METAR" | "TAF" | "PIREP"; station: string | null; time: string | null; raw: string;
  flight_category: string | null; decoded: MetarDecoded | TafDecoded | PirepDecoded | null };

export type StationMetar = { valid: string; kind: string; raw: string; flight_category: string | null;
  decoded: MetarDecoded | null; temp_c: number | null; dewpoint_c: number | null; wind_dir: number | null;
  wind_kt: number | null; gust_kt: number | null; vis_mi: number | null; altimeter_in: number | null;
  product_id: number };
export type StationDetail = {
  station: { id: string; icao?: string; faa?: string; name?: string; state?: string; country?: string;
             elev_m?: number; lon?: number; lat?: number };
  metars: StationMetar[];
  taf: null | { issued: string | null; valid_from: string | null; valid_to: string | null; raw: string;
                product_id: number; current: boolean; decoded: TafDecoded };
};

export type ProductType = { nnn: string; name: string; category: string; n: number; last: string };

export type ImageProduct = { product: string; kind: "band" | "rgb" | "graphic"; label: string; overlay: boolean; plain: boolean;
  n: number; first: string; last: string };
export type ImageSource = { source: string; sector: string; group: "satellite" | "nws"; last: string;
  products: ImageProduct[] };
export type Catalog = { sources: ImageSource[]; queue: { pending: number; error: number } };
export type Frame = { id: number; scan_time: string; display: string; thumb: string; width: number; height: number;
  orig: string | null };
export type LoopQuery = { source: string; sector: string; product: string; overlay: boolean; hours: number };

export type ZoneInfo = { kind: string; ugc: string; name: string; state: string | null; cwa: string | null };
export type ZoneHazard = { office: string; phenomena: string; significance: string; etn: number; action: string;
  begins: string | null; ends: string | null; product_id: number; issued: string; storm_based: boolean;
  name: string; color: string };
export type ZoneAt = {
  lon: number; lat: number; zones: ZoneInfo[];
  zone: (ZoneInfo & { geometry: GeoJSON.Geometry }) | null;
  forecast: null | { product_id: number; nnn: string; awips: string; office: string; title: string;
    issued: string | null; received: string; ugc_expire: string | null; headlines: string[]; body: string };
  hazards: ZoneHazard[];
  products: { id: number; nnn: string; awips: string; office: string; title: string; issued: string | null;
    received: string; type_name: string }[];
};

export type Filters = { q?: string; category?: string[]; nnn?: string[]; office?: string; ugc?: string };

async function get<T>(path: string, params?: Record<string, string | string[] | number | undefined>): Promise<T> {
  const u = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === "") continue;
    for (const x of Array.isArray(v) ? v : [v]) u.searchParams.append(k, String(x));
  }
  const r = await fetch(u);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const api = {
  categories: () => get<Record<string, string>>("/api/categories"),
  types: (hours = 24) => get<ProductType[]>("/api/products/types", { hours }),
  products: (f: Filters, cursor?: Page["next"], limit = 50) =>
    get<Page>("/api/products", { ...f, limit, before: cursor?.before, before_id: cursor?.before_id }),
  product: (id: number) => get<ProductDetail>(`/api/products/${id}`),
  warnings: () => get<GeoJSON.FeatureCollection>("/api/warnings/active"),
  discussions: () => get<GeoJSON.FeatureCollection>("/api/discussions/active"),
  stations: (q: { bbox?: string; zoom?: number; hours?: number }) =>
    get<GeoJSON.FeatureCollection>("/api/stations/latest", q),
  station: (id: string) => get<StationDetail>(`/api/stations/${encodeURIComponent(id)}`),
  pireps: (hours = 3) => get<GeoJSON.FeatureCollection>("/api/pireps/recent", { hours }),
  zoneAt: (lon: number, lat: number) => get<ZoneAt>("/api/zones/at", { lon, lat }),
  imageCatalog: () => get<Catalog>("/api/imagery/catalog"),
  frames: (q: LoopQuery, limit?: number) =>
    get<{ frames: Frame[] }>("/api/imagery/frames", { ...q, overlay: String(q.overlay), limit })
      .then((r) => r.frames),
};

// "23:54Z · 4:54 PM PDT"
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const z = d.toISOString().slice(11, 16) + "Z";
  const local = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  return `${z} · ${local}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
