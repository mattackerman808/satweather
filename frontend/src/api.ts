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
};

export type ProductType = { nnn: string; name: string; category: string; n: number; last: string };

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
