// Lapisan data GoldPulse — 100% client-side, tanpa backend.
// Sumber: JSON di repo GitHub (public, CORS terbuka) + spot live XAUS.com.

export const REPO = "ponimazino/gold";
export const DATA_BASE = `https://raw.githubusercontent.com/${REPO}/main/data`;
export const SPOT_API = "https://xaus.com/api/v1/spot?compact=1";

const WIB = "Asia/Jakarta";

// ---- tipe (defensif: semua field opsional kecuali kunci) ----

export interface Bar { t: string; o: number; h: number; l: number; c: number }
export interface Coverage { from?: string; to?: string; bars?: number }
export interface Meta {
  status?: string; message?: string;
  updated_at_wib?: string;
  coverage?: Record<string, Coverage>;
}
export interface PatternStat {
  tf: string; pattern: string;
  n?: number; resolved?: number; win_rate?: number | null;
  win_rate_lo?: number | null; win_rate_hi?: number | null;
  timeout_pct?: number | null; avg_r?: number | null;
  oos_n?: number; oos_win_rate?: number | null;
  safe_n?: number; safe_win_rate?: number | null;
  safe_oos_n?: number; safe_oos_win_rate?: number | null;
  cost_win_rate?: number | null; cost_oos_win_rate?: number | null;
  safe_cost_win_rate?: number | null; safe_cost_oos_win_rate?: number | null;
}
export interface CalEvent {
  title?: string; t_utc?: string;
  forecast?: string | null; previous?: string | null; actual?: string | null;
}
export interface NewsItem { title?: string; url?: string; source?: string; published_utc?: string }
export interface Levels { entry: number; sl: number; tp1: number; tp2: number; atr14: number }
export interface LevelsSafe { entry: number; sl: number; tp1: number; atr14: number }
export interface Recommendation {
  id?: string; created_at_wib?: string;
  status?: "entry" | "tunggu" | "netral";
  bias?: "bullish" | "bearish" | "netral";
  confidence?: number | null;
  confidence_note?: string | null;
  levels?: Levels | null;
  levels_safe?: LevelsSafe | null;
  confidence_safe?: number | null;
  pattern?: string | null;
  direction?: number;
  h4_context?: { trend?: string; rsi14?: number } | null;
  blackout?: { title?: string; t_utc?: string; minutes_to_event?: number } | null;
  next_event?: { title?: string; t_utc?: string } | null;
  rationale?: string[];
}
export interface TrackingStats {
  total?: number; wins?: number; losses?: number; timeouts?: number;
  active?: number; resolved?: number; hit_rate?: number | null;
}
export interface TrackingEntry {
  id?: string; created_at_wib?: string; status?: string;
  bias?: string; pattern?: string;
  confidence?: number | null;
  direction?: number;
  levels?: Levels | null;
  rationale?: string[];
  resolved_at_wib?: string;
  outcome?: {
    bars_held?: number; mfe_usd?: number; mae_usd?: number;
    events?: string[]; why?: string; resolved_at_wib?: string;
  } | null;
}
export interface EodEntry {
  pattern?: string | null; bias?: string; status?: string;
  entry_at_wib?: string; resolved_at_wib?: string | null;
  mfe_usd?: number | null; mae_usd?: number | null;
  events?: string[]; why?: string | null;
}
export interface EodDay {
  date?: string; entries?: EodEntry[];
}
export interface Tracking {
  updated_at_wib?: string;
  stats?: TrackingStats;
  history?: TrackingEntry[];
  eod?: EodDay[];
}

export interface DashboardData {
  meta: Meta | null;
  bars1h: Bar[];
  bars4h: Bar[];
  patterns: { generated_at_wib?: string; params?: Record<string, unknown>; results?: PatternStat[] } | null;
  calendar: { source?: string; updated_at_wib?: string; events?: CalEvent[] } | null;
  news: { updated_at_wib?: string; items?: NewsItem[] } | null;
  recommendation: Recommendation | null;
  tracking: Tracking | null;
}

// ---- fetch helpers ----

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null; // offline / file belum ada -> view menangani null
  }
}

export async function loadDashboard(): Promise<DashboardData> {
  const [meta, c1, c4, patterns, calendar, news, recommendation, tracking] =
    await Promise.all([
      getJson<Meta>(`${DATA_BASE}/meta.json`),
      getJson<{ bars?: Bar[] }>(`${DATA_BASE}/xauusd_1h.json`),
      getJson<{ bars?: Bar[] }>(`${DATA_BASE}/xauusd_4h.json`),
      getJson<DashboardData["patterns"]>(`${DATA_BASE}/patterns.json`),
      getJson<DashboardData["calendar"]>(`${DATA_BASE}/calendar.json`),
      getJson<DashboardData["news"]>(`${DATA_BASE}/news.json`),
      getJson<Recommendation>(`${DATA_BASE}/recommendation.json`),
      getJson<Tracking>(`${DATA_BASE}/tracking.json`),
    ]);
  return {
    meta,
    bars1h: c1?.bars ?? [],
    bars4h: c4?.bars ?? [],
    patterns,
    calendar,
    news,
    recommendation,
    tracking,
  };
}

// ---- laporan mingguan PDF (dibuat job report.yml tiap Senin pagi WIB) ----

export const REPORTS_BASE = `${DATA_BASE}/reports`;
// PDF dibuka via jsDelivr: raw.githubusercontent mengirim Content-Type
// octet-stream (browser otomatis DOWNLOAD), jsDelivr mengirim application/pdf
// (browser VIEW langsung). Cache CDN ~12 jam — cukup untuk laporan mingguan.
export const PDF_BASE = "https://cdn.jsdelivr.net/gh/ponimazino/gold@main/data/reports";
// v = cache buster (mis. ukuran KB) supaya regenerasi PDF hari yang sama
// tidak tersangkut cache browser pada filename identik.
export const reportUrl = (name: string, v?: number | string) =>
  `${PDF_BASE}/${name}${v != null ? `?v=${encodeURIComponent(String(v))}` : ""}`;

export interface ReportFile {
  name?: string; date_wib?: string; kb?: number;
  summary?: string; rec_status?: string;
}
export interface ReportsIndex { updated_at_wib?: string; files?: ReportFile[] }

export async function loadReports(): Promise<ReportsIndex | null> {
  return getJson<ReportsIndex>(`${REPORTS_BASE}/index.json`);
}

// ---- spot live (XAUS.com, tanpa key, hanya display) ----

export interface Spot { price: number; at: number; live: boolean }

export async function fetchSpot(): Promise<Spot | null> {
  try {
    const res = await fetch(SPOT_API, { cache: "no-store" });
    const j = await res.json();
    const price = j?.xau?.price;
    if (typeof price !== "number") return null;
    return { price, at: Date.now(), live: true };
  } catch {
    return null;
  }
}

// ---- helper waktu & angka ----

export function formatWib(ts: number, withDate = true): string {
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone: WIB,
    day: withDate ? "2-digit" : undefined,
    month: withDate ? "short" : undefined,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return f.format(ts) + " WIB";
}

export function utcStringToTs(s?: string): number {
  if (!s) return NaN;
  const ms = Date.parse(s.includes("T") ? s : s.replace(" ", "T") + "Z");
  return ms;
}

export function pctChange(current: number, prior: number): number {
  if (!prior) return 0;
  return ((current - prior) / prior) * 100;
}

export function fmtUsd(v?: number | null, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return "$" + v.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

// ---- indikator client-side (EMA untuk overlay chart) ----

export function ema(values: number[], period: number): number[] {
  const out: number[] = [];
  const k = 2 / (period + 1);
  let prev: number | undefined;
  for (const v of values) {
    prev = prev === undefined ? v : v * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

// skor tren 0-100 transparan (dipakai kartu "4H trend score")
export function scoreScenario(input: {
  trendAligned: boolean; momentumSupportive: boolean;
  macroRisk: "low" | "medium" | "high";
}): number {
  let score = 50;
  if (input.trendAligned) score += 14;
  if (input.momentumSupportive) score += 8;
  if (input.macroRisk === "low") score += 6;
  if (input.macroRisk === "medium") score -= 2;
  if (input.macroRisk === "high") score -= 10;
  return Math.max(0, Math.min(100, score));
}

// jeda entry identik backend: 2 jam sebelum - 1 jam sesudah rilis
export const BLACKOUT_BEFORE_MS = 2 * 3_600_000;
export const BLACKOUT_AFTER_MS = 1 * 3_600_000;

export function activeBlackout(events: CalEvent[], now: number): CalEvent | null {
  for (const e of events) {
    const t = utcStringToTs(e.t_utc);
    if (Number.isNaN(t)) continue;
    if (t - BLACKOUT_BEFORE_MS <= now && now <= t + BLACKOUT_AFTER_MS) return e;
  }
  return null;
}

export function countdown(msDiff: number): string {
  const abs = Math.abs(msDiff);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.round((abs % 3_600_000) / 60_000);
  const unit = h > 0 ? `${h}j ${m}m` : `${m}m`;
  return msDiff >= 0 ? `dalam ${unit}` : `${unit} lalu`;
}

export const PATTERN_NAMES: Record<string, string> = {
  bullish_engulfing: "Bullish Engulfing",
  bearish_engulfing: "Bearish Engulfing",
  hammer: "Hammer (pin bar)",
  shooting_star: "Shooting Star",
  inside_bar: "Inside Bar",
};

// candle untuk recharts: label WIB + ema20/50 overlay
export interface ChartRow {
  ts: number; label: string; open: number; high: number; low: number;
  close: number; ema20: number; ema50: number;
  up: boolean;
  // 4 segmen candle SATU stack (bawah → atas): base transparan, sumbu
  // bawah, badan, sumbu atas — recharts menumpuknya jadi 1 candle per bar.
  cBase: number; cWickLower: number; cBody: number; cWickUpper: number;
}

export function toChartRows(bars: Bar[]): ChartRow[] {
  const closes = bars.map((b) => b.c);
  const e20 = ema(closes, 20);
  const e50 = ema(closes, 50);
  const fmtDay = new Intl.DateTimeFormat("en-GB", {
    timeZone: WIB, day: "2-digit", month: "short",
  });
  return bars.map((b, i) => {
    const up = b.c >= b.o;
    return {
      ts: utcStringToTs(b.t),
      label: fmtDay.format(utcStringToTs(b.t)),
      open: b.o, high: b.h, low: b.l, close: b.c,
      ema20: e20[i], ema50: e50[i],
      up,
      // segmen candle untuk satu stack: 0→low (transparan), low→min(o,c),
      // min(o,c)→max(o,c), max(o,c)→high
      cBase: b.l,
      cWickLower: Math.min(b.o, b.c) - b.l,
      cBody: Math.abs(b.c - b.o),
      cWickUpper: b.h - Math.max(b.o, b.c),
    };
  });
}