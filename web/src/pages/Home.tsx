import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Crosshair,
  Database,
  ExternalLink,
  FileCheck2,
  FileText,
  FlaskConical,
  Gauge,
  Globe2,
  History,
  LayoutDashboard,
  Menu,
  Newspaper,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  TimerReset,
  TrendingUp,
  X,
  Zap,
} from "lucide-react";
import {
  activeBlackout,
  countdown,
  DashboardData,
  fetchSpot,
  fmtUsd,
  formatWib,
  loadDashboard,
  loadReports,
  PATTERN_NAMES,
  pctChange,
  reportUrl,
  ReportFile,
  ReportsIndex,
  scoreScenario,
  Spot,
  toChartRows,
  utcStringToTs,
} from "../data";

type ViewKey = "summary" | "overview" | "analysis" | "backtest" | "riwayat" | "calendar" | "jadwal";
type Timeframe = "4H" | "1H";

const SOURCE_LINKS = {
  reuters: "https://www.reuters.com/world/",
  tradingEconomics: "https://tradingeconomics.com/calendar",
  twelveData: "https://twelvedata.com/",
  xaus: "https://xaus.com/",
};

const acceptedSources = [
  { name: "Twelve Data", note: "OHLCV H1/H4 historis 3 tahun — backfill + sync per jam", href: SOURCE_LINKS.twelveData },
  { name: "XAUS.com", note: "Spot live untuk kartu harga (display only, tidak disimpan)", href: SOURCE_LINKS.xaus },
  { name: "Trading Economics", note: "Kalender event bintang-3, US saja", href: SOURCE_LINKS.tradingEconomics },
  { name: "Reuters & wire terverifikasi", note: "Berita breaking — tanpa opini, whitelist sumber", href: SOURCE_LINKS.reuters },
];

const navItems: { key: ViewKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "summary", label: "Apa kata hari ini", icon: Sparkles },
  { key: "analysis", label: "Daily analysis", icon: Crosshair },
  { key: "backtest", label: "Backtest lab", icon: FlaskConical },
  { key: "riwayat", label: "Riwayat", icon: History },
  { key: "calendar", label: "Fundamentals", icon: Newspaper },
  { key: "jadwal", label: "Jadwal", icon: Clock3 },
];

function StatusPill({ tone = "green", children }: { tone?: "green" | "amber" | "violet" | "slate"; children: React.ReactNode }) {
  return <span className={`status-pill ${tone}`}><span className="status-dot" />{children}</span>;
}

function MetricCard({ label, value, change, icon: Icon, tone = "violet", footnote }: { label: string; value: string; change?: string; icon: typeof Activity; tone?: string; footnote?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-topline"><span>{label}</span><span className={`metric-icon ${tone}`}><Icon size={16} /></span></div>
      <div className="metric-value-row"><strong>{value}</strong>{change && <span className={change.startsWith("+") ? "positive" : "negative"}>{change}</span>}</div>
      {footnote && <p>{footnote}</p>}
    </div>
  );
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ payload: { close: number; high: number; low: number; ts: number } }>; label?: string }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="tooltip-label">{label} · {formatWib(point.ts, false)}</div>
      <div className="tooltip-price">{fmtUsd(point.close)}</div>
      <div className="tooltip-grid"><span>H {point.high.toFixed(2)}</span><span>L {point.low.toFixed(2)}</span></div>
    </div>
  );
}

// ---- data hook: dashboard + spot live + jam WIB ----

function useDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [spot, setSpot] = useState<Spot | null>(null);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());

  const refresh = () => {
    loadDashboard().then((d) => {
      setData(d);
      setLoading(false);
    });
    fetchSpot().then((s) => s && setSpot(s));
  };

  useEffect(() => {
    refresh();
    const spotTimer = setInterval(() => fetchSpot().then((s) => s && setSpot(s)), 30_000);
    const clockTimer = setInterval(() => setNow(Date.now()), 30_000);
    const dataTimer = setInterval(refresh, 10 * 60_000);
    return () => { clearInterval(spotTimer); clearInterval(clockTimer); clearInterval(dataTimer); };
  }, []);

  return { data, spot, loading, now, refresh };
}

function useWibClock() {
  const [tick, setTick] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setTick(Date.now()), 20_000);
    return () => clearInterval(t);
  }, []);
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hour12: false }).format(tick);
}

// ---- chart harga nyata (H4 prioritas / H1) — bisa digeser & di-zoom ----
//
// Interaksi: scroll = zoom (anchor di posisi kursor), klik-tarik = geser
// periode, tombol +/-/reset untuk sentuh (mobile). Jendela default: 90 bar
// untuk line, 48 bar untuk candle (badan lebih lebar).

const CHART_LOAD = 1200;  // bar maksimum yang dimuat ke grafik
const CHART_MIN_BARS = 15;
const LINE_BARS = 90;   // jendela default mode line
const CANDLE_BARS = 48; // jendela candle lebih pendek supaya badan lebih lebar

// Bentuk candlestick: semua segmen dalam satu stack berbagi lebar band yang
// sama (recharts mengabaikan barSize per-Bar dalam satu stack), jadi sumbu
// dipaksa tipis lewat custom shape. Data bar di-spread ke props shape,
// termasuk flag `up`.
type CandleShapeProps = { x?: number; y?: number; width?: number; height?: number; up?: boolean };
const candleFill = (p: CandleShapeProps) => (p.up ? "#d5ff3f" : "#ff7799");

function CandleBody(p: CandleShapeProps) {
  const w = p.width ?? 0;
  const h = Math.max(p.height ?? 0, 1.5); // doji tetap terlihat 1.5px
  return <rect x={p.x ?? 0} y={p.y ?? 0} width={w} height={h} fill={candleFill(p)} />;
}

function CandleWick(p: CandleShapeProps) {
  const bw = p.width ?? 0;
  const w = Math.max(1.2, Math.min(2.2, bw * 0.16));
  return <rect x={(p.x ?? 0) + (bw - w) / 2} y={p.y ?? 0} width={w} height={p.height ?? 0} fill={candleFill(p)} />;
}

function PriceChart({ data, spot }: { data: DashboardData; spot: Spot | null }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("4H");
  const [mode, setMode] = useState<"line" | "candle">("line");
  const chartBoxRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ chartX: number; start: number } | null>(null);

  // rows penuh (EMA dihitung atas seluruh window supaya konsisten saat digeser)
  const allRows = useMemo(() => {
    const bars = timeframe === "4H" ? data.bars4h : data.bars1h;
    return toChartRows(bars.slice(-CHART_LOAD));
  }, [data, timeframe]);

  const MAX = allRows.length;
  const defaultCount = Math.min(mode === "candle" ? CANDLE_BARS : LINE_BARS, MAX);
  const defaultRange = useMemo(() => ({ start: Math.max(0, MAX - defaultCount), count: defaultCount }), [MAX, defaultCount]);
  const [range, setRange] = useState(defaultRange);
  useEffect(() => setRange(defaultRange), [defaultRange]); // reset saat ganti TF / data baru

  const plotWidth = () => Math.max(100, (chartBoxRef.current?.clientWidth ?? 300) - 60);

  const clampRange = (start: number, count: number) => {
    const c = Math.max(CHART_MIN_BARS, Math.min(MAX, count));
    const s = Math.max(0, Math.min(MAX - c, start));
    return { start: s, count: c };
  };

  const rows = useMemo(() => {
    const arr = allRows.slice(range.start, range.start + range.count);
    // spot live menimpa close candle terakhir (display saja) bila terlihat
    if (spot && arr.length && range.start + range.count >= MAX) {
      const last = arr[arr.length - 1];
      last.close = spot.price;
      last.high = Math.max(last.high, spot.price);
      last.low = Math.min(last.low, spot.price);
      // segmen candle ikut nilai baru supaya mode candle tidak basi
      const bodyLow = Math.min(last.open, last.close);
      const bodyHigh = Math.max(last.open, last.close);
      last.cBase = last.low;
      last.cWickLower = bodyLow - last.low;
      last.cBody = bodyHigh - bodyLow;
      last.cWickUpper = last.high - bodyHigh;
    }
    return arr;
  }, [allRows, range, spot?.price, MAX]);

  // zoom via scroll — anchor tetap di posisi kursor
  useEffect(() => {
    const el = chartBoxRef.current;
    if (!el || !MAX) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const pw = plotWidth();
      const f = Math.min(1, Math.max(0, ev.offsetX / pw));
      setRange((r) => {
        const factor = ev.deltaY > 0 ? 1.18 : 0.85; // scroll bawah = zoom out
        const anchor = r.start + f * r.count;
        const count = Math.round(r.count * factor);
        return clampRange(Math.round(anchor - f * count), count);
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [MAX]);

  const zoomBy = (factor: number) => setRange((r) => {
    const anchor = r.start + r.count / 2;
    const count = Math.round(r.count * factor);
    return clampRange(Math.round(anchor - count / 2), count);
  });

  const rec = data.recommendation;
  const lv = rec?.levels ?? null;
  const latest = allRows[allRows.length - 1];
  if (!latest) return <div className="chart-footnote"><span>Data candle belum tersedia.</span></div>;
  const previous = allRows[allRows.length - 2] ?? latest;
  const visible = rows.length ? rows : allRows;
  const min = Math.floor(Math.min(...visible.map((d) => d.low)) - 8);
  const max = Math.ceil(Math.max(...visible.map((d) => d.high)) + 8);
  // tick X: jumlah berbasis lebar piksel (±1 label / 84px) + dedup label
  // berurutan yang sama, supaya tidak bertumpukan saat zoom in/out.
  const maxTicks = Math.max(3, Math.floor(plotWidth() / 84));
  const tickEvery = Math.max(1, Math.ceil(visible.length / maxTicks));
  const labels: string[] = [];
  let lastLabel = "";
  for (let i = 0; i < visible.length; i += tickEvery) {
    const lab = visible[i].label;
    if (lab !== lastLabel) { labels.push(lab); lastLabel = lab; }
  }
  const delta = pctChange(latest.close, previous.close);
  const atRightEdge = range.start + range.count >= MAX;

  return (
    <div className="chart-wrap">
      <div className="chart-legend-row">
        {mode === "line" ? (<>
          <div className="legend-item"><span className="legend-line lime" />Price</div>
          <div className="legend-item"><span className="legend-line purple" />EMA 20</div>
          <div className="legend-item"><span className="legend-line gray" />EMA 50</div>
        </>) : (<>
          <div className="legend-item"><span className="legend-line lime" />Up candle</div>
          <div className="legend-item"><span className="legend-line red" />Down candle</div>
        </>)}
        <div className="chart-tools">
          <div className="chart-mode-seg" role="group" aria-label="Tipe chart">
            <button className={mode === "line" ? "active" : ""} onClick={() => setMode("line")}>Line</button>
            <button className={mode === "candle" ? "active" : ""} onClick={() => setMode("candle")}>Candle</button>
          </div>
          <button className="zoom-btn" aria-label="Zoom out" onClick={() => zoomBy(1.3)}>−</button>
          <button className="zoom-btn" aria-label="Zoom in" onClick={() => zoomBy(0.7)}>+</button>
          <button className="zoom-btn" aria-label="Reset jendela" onClick={() => setRange(defaultRange)}>⟲</button>
          <span className="chart-range">{visible[0].label} — {visible[visible.length - 1].label} · {visible.length} bar</span>
        </div>
      </div>
      <div className="price-chart" ref={chartBoxRef} style={{ cursor: "grab", touchAction: "pan-y" }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={visible} margin={{ top: 18, right: 12, left: -12, bottom: 0 }}
            onMouseDown={(e: { chartX?: number }) => { if (e?.chartX != null) dragRef.current = { chartX: e.chartX, start: range.start }; }}
            onMouseMove={(e: { chartX?: number }) => {
              const d = dragRef.current;
              if (!d || e?.chartX == null) return;
              const shift = Math.round((e.chartX - d.chartX) / (plotWidth() / range.count));
              const next = clampRange(d.start - shift, range.count);
              setRange((r) => (next.start !== r.start ? next : r));
            }}
            onMouseUp={() => { dragRef.current = null; }}
            onMouseLeave={() => { dragRef.current = null; }}
          >
            <defs>
              <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#d5ff3f" stopOpacity={0.28} /><stop offset="100%" stopColor="#d5ff3f" stopOpacity={0} /></linearGradient>
            </defs>
            <CartesianGrid stroke="#25252f" vertical={false} />
            <XAxis dataKey="label" ticks={labels} interval={0} tick={{ fill: "#686873", fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis domain={[min, max]} tick={{ fill: "#686873", fontSize: 10 }} axisLine={false} tickLine={false} width={48} tickFormatter={(v: number) => `$${v}`} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#747482", strokeDasharray: "4 4" }} />
            {lv && timeframe === "4H" && atRightEdge && (
              <ReferenceArea y1={lv.entry - lv.atr14 * 0.5} y2={lv.entry + lv.atr14 * 0.5} fill="#d5ff3f" fillOpacity={0.08} strokeOpacity={0} />
            )}
            {/* NOTE: jangan bungkus <Bar> dalam fragment <></> — recharts
                (react-is 18 di bawah React 19) tidak me-flatten fragment,
                sehingga bar tidak terdeteksi. Pakai child kondisional langsung. */}
            {mode === "line" ? (
              <Area type="monotone" dataKey="close" stroke="#d5ff3f" strokeWidth={2.4} fill="url(#priceFill)" dot={false} activeDot={{ r: 4, fill: "#d5ff3f", stroke: "#16161c", strokeWidth: 2 }} />
            ) : (
              <Bar dataKey="cBase" stackId="candle" fill="transparent" isAnimationActive={false} />
            )}
            {mode === "candle" && (
              <Bar dataKey="cWickLower" stackId="candle" isAnimationActive={false} shape={CandleWick} />
            )}
            {mode === "candle" && (
              <Bar dataKey="cBody" stackId="candle" isAnimationActive={false} shape={CandleBody} />
            )}
            {mode === "candle" && (
              <Bar dataKey="cWickUpper" stackId="candle" isAnimationActive={false} shape={CandleWick} />
            )}
            <Line type="monotone" dataKey="ema20" stroke="#a58bff" strokeWidth={1.4} dot={false} />
            <Line type="monotone" dataKey="ema50" stroke="#7a7a86" strokeWidth={1.2} strokeDasharray="5 5" dot={false} />
            {atRightEdge && (
              <ReferenceLine y={latest.close} stroke="#d5ff3f" strokeDasharray="3 4" strokeOpacity={0.55} label={{ value: fmtUsd(latest.close), position: "insideTopRight", fill: "#d5ff3f", fontSize: 11 }} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-footnote">
        <span><Clock3 size={13} /> Candle terakhir buka {formatWib(latest.ts)} · data disinkron {data.meta?.updated_at_wib ?? "—"} (tiap jam :17 WIB)</span>
        <span className={latest.close >= previous.close ? "positive" : "negative"}>
          {latest.close >= previous.close ? "▲" : "▼"} {Math.abs(delta).toFixed(2)}% vs candle sebelumnya
        </span>
        <span style={{ color: "#666674" }}>Scroll = zoom · tarik = geser sejarah</span>
      </div>
      <div className="segmented" style={{ marginTop: 10, alignSelf: "flex-start", display: "inline-flex" }}>
        <button className={timeframe === "4H" ? "active" : ""} onClick={() => setTimeframe("4H")}>4H <em>priority</em></button>
        <button className={timeframe === "1H" ? "active" : ""} onClick={() => setTimeframe("1H")}>1H</button>
      </div>
    </div>
  );
}

function MiniSparkline({ points }: { points: number[] }) {
  const data = points.map((value, x) => ({ x, value }));
  const positive = (points[points.length - 1] ?? 0) >= (points[0] ?? 0);
  return (
    <div className="mini-spark"><ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data}>
        <defs>
          <linearGradient id={`mini-${positive ? "p" : "n"}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={positive ? "#d5ff3f" : "#ff7799"} stopOpacity={0.28} />
            <stop offset="100%" stopColor={positive ? "#d5ff3f" : "#ff7799"} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="value" stroke={positive ? "#d5ff3f" : "#ff7799"} fill={`url(#mini-${positive ? "p" : "n"})`} strokeWidth={1.7} dot={false} />
      </AreaChart>
    </ResponsiveContainer></div>
  );
}

// ---- view: Ringkasan (bahasa awam) ----

function plainDir(bias?: string): string {
  return bias === "bullish" ? "cenderung NAIK" : bias === "bearish" ? "cenderung TURUN" : "mendatar / belum jelas";
}

function confidenceVerdict(c: number): { label: string; tone: "green" | "amber" | "slate"; text: string } {
  if (c >= 60) return { label: "Cukup menjanjikan", tone: "green", text: `Secara historis, kondisi serupa kena target ${c} dari 100 kali. Peluang bagus — tapi ${100 - c} kali tetap gagal, jadi batas rugi wajib.` };
  if (c >= 50) return { label: "Seimbang", tone: "amber", text: `Kira-kira ${c} dari 100 kondisi serupa berhasil, ${100 - c} gagal. Ini seperti lemparan koin yang sedikit miring — hati-hati dan pakai ukuran posisi kecil.` };
  return { label: "Risiko lebih besar dari peluang", tone: "slate", text: `Jujur: dari 100 kondisi serupa di masa lalu, hanya sekitar ${c} yang kena target — ${100 - c} gagal. Secara historis setup ini LEBIH SERING GAGAL. Sistem tetap menampilkannya apa adanya, bukan sebagai saran untuk masuk.` };
}

function SummaryView({ data, spot, now, onNavigate }: { data: DashboardData; spot: Spot | null; now: number; onNavigate: (v: ViewKey) => void }) {
  const rec = data.recommendation ?? {};
  const status = rec.status ?? "netral";
  const bias = rec.bias ?? "netral";
  const lv = rec.levels ?? null;
  const confidence = rec.confidence != null ? Math.round(rec.confidence * 100) : null;
  const verdict = confidence != null ? confidenceVerdict(confidence) : null;
  const stats = data.tracking?.stats;
  const hitRate = stats?.hit_rate != null ? Math.round(stats.hit_rate * 100) : null;
  const news = (data.news?.items ?? []).slice(0, 3);
  const nextEvents = (data.calendar?.events ?? [])
    .map((e) => ({ e, t: utcStringToTs(e.t_utc) }))
    .filter(({ t }) => !Number.isNaN(t) && t > now - 24 * 3_600_000)
    .sort((a, b) => a.t - b.t)
    .slice(0, 3);
  const livePrice = spot?.price;

  const statusInfo =
    status === "entry"
      ? { title: "Ada peluang entry hari ini", tone: "green" as const, icon: TrendingUp, copy: `Sistem melihat pola yang layak dipertimbangkan. Arahnya ${plainDir(bias)}. Tapi ingat: "layak dipertimbangkan" bukan "pasti untung" — baca batas ruginya di bawah.` }
      : status === "tunggu"
        ? { title: "Tunda dulu — sedang ada rilis data besar", tone: "amber" as const, icon: TimerReset, copy: `Rilis ${rec.blackout?.title ?? "data ekonomi AS"} sedang/akan berlangsung. Harga emas biasanya bergerak liar saat ini, jadi sistem menahan semua rekomendasi. Tunggu sampai jeda berlalu.` }
        : { title: "Tidak ada peluang hari ini — diam itu oke", tone: "slate" as const, icon: ShieldAlert, copy: "Aturan sistem (pola H1 searah tren H4) tidak terpenuhi hari ini. Tidak masuk pasar adalah keputusan yang valid, dan sering kali yang paling menguntungkan." };

  const marketExplain =
    rec.h4_context?.trend === "up" ? "Beberapa hari terakhir harga emas bergerak NAIK (uptrend) — pembeli masih lebih kuat."
      : rec.h4_context?.trend === "down" ? "Beberapa hari terakhir harga emas bergerak TURUN (downtrend) — penjual lebih kuat."
        : "Harga emas bergerak MENDATAR — pembeli dan penjual seimbang, arah belum jelas.";
  const rsiExplain = rec.h4_context?.rsi14 != null
    ? rec.h4_context.rsi14 > 70 ? `Momentum sudah terlalu panas (RSI ${rec.h4_context.rsi14.toFixed(0)}) — naik terus tapi rawan tiba-tiba turun.`
      : rec.h4_context.rsi14 < 45 ? `Momentum lemah (RSI ${rec.h4_context.rsi14.toFixed(0)}) — tekanan jual masih dominan.`
        : `Momentum tergolong sehat (RSI ${rec.h4_context.rsi14.toFixed(0)}), belum terlalu panas.`
    : "";

  const riskUsd = lv ? Math.abs(lv.entry - lv.sl) : null;
  const rewardUsd = lv ? Math.abs(lv.tp1 - lv.entry) : null;

  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><Sparkles size={13} /> Ringkasan harian <span className="eyebrow-separator">/</span> {new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", weekday: "long", day: "2-digit", month: "long", year: "numeric" }).format(now)} WIB</div>
        <h1>Apa kata data <span>hari ini.</span></h1>
        <p className="hero-subtitle">Versi sederhana tanpa istilah teknis — untuk tahu arah emas dan apakah ada peluang, dalam 1 menit bacaan.</p>
      </div>
      <div className="hero-actions">
        {livePrice != null && <StatusPill tone="green">XAUUSD {fmtUsd(livePrice)}</StatusPill>}
        <button className="secondary-button" onClick={() => onNavigate("analysis")}>Detail teknis <ArrowUpRight size={15} /></button>
      </div>
    </section>

    <div className="signal-banner">
      <div className="signal-mark"><statusInfo.icon size={20} /></div>
      <div className="signal-copy">
        <div className="signal-title"><StatusPill tone={statusInfo.tone}>{status === "entry" ? "Peluang" : status === "tunggu" ? "Tunda" : "Diam"}</StatusPill> {statusInfo.title}</div>
        <p>{statusInfo.copy}</p>
      </div>
      {confidence != null && <div className="signal-score"><span>Peluang historis</span><strong>{confidence}<span>%</span></strong></div>}
    </div>

    <section className="metric-grid">
      <MetricCard label="Arah pasar (H4)" value={bias === "bullish" ? "Naik" : bias === "bearish" ? "Turun" : "Mendatar"} icon={TrendingUp} tone={bias === "bullish" ? "lime" : bias === "bearish" ? "amber" : "slate"} footnote={marketExplain} />
      <MetricCard label="Status rekomendasi" value={status === "entry" ? "Ada setup" : status === "tunggu" ? "Tunggu event" : "Tidak ada setup"} icon={statusInfo.icon} footnote={status === "entry" ? "Level aktif — lihat kartu di bawah" : status === "tunggu" ? "Jeda rilis −2 jam s/d +1 jam" : "Kembali cek besok pagi"} />
      <MetricCard label="Peluang (menurut sejarah)" value={confidence != null ? `${confidence}%` : "—"} icon={Gauge} tone={confidence != null && confidence >= 55 ? "lime" : "amber"} footnote={verdict?.label ?? "Belum ada statistik"} />
      <MetricCard label="Rekam jejak sistem" value={hitRate != null ? `${hitRate}% kena target` : "Belum ada data"} icon={FileCheck2} tone="slate" footnote={`${stats?.resolved ?? 0} dari ${stats?.total ?? 0} rekomendasi sudah dinilai jujur`} />
    </section>

    {lv && (
      <div className="panel" style={{ marginBottom: 0 }}>
        <div className="panel-header"><div><div className="panel-kicker">Kalau mau ikut rekomendasi ini</div><h2>Risiko &amp; target dalam angka sederhana</h2></div><Crosshair size={18} className="amber-icon" /></div>
        <div className="level-grid">
          <div className="level-card entry"><span>Harga masuk (sekitar)</span><strong>{fmtUsd(lv.entry)}</strong><small>Bukan harus persis — area beli/jual, bukan titik sakti</small></div>
          <div className="level-card target"><span>Target pertama</span><strong>{fmtUsd(lv.tp1)}</strong><small>{rewardUsd != null ? `Untung sekitar ${fmtUsd(rewardUsd)} per ounce bila kena` : ""}</small></div>
          <div className="level-card invalidation"><span>Batas rugi (SL)</span><strong>{fmtUsd(lv.sl)}</strong><small>{riskUsd != null ? `Kena = rugi sekitar ${fmtUsd(riskUsd)} per ounce. Wajib pasang, tanpa syarat` : ""}</small></div>
        </div>
        <div className="risk-note" style={{ marginTop: 14 }}>
          <ShieldAlert size={15} /><span>Perbandingan sederhana: rugi sekitar {fmtUsd(riskUsd ?? 0, 2)} vs potensi untung {fmtUsd(rewardUsd ?? 0, 2)} — rasio 1 : {riskUsd ? (rewardUsd! / riskUsd).toFixed(1) : "—"}. Kalau rasio ini tidak masuk akal buat kamu, jangan masuk.</span>
        </div>
        {rec.levels_safe && rec.confidence_safe != null && (
          <div className="risk-note" style={{ marginTop: 10 }}>
            <ShieldAlert size={15} /><span><b>Mode aman:</b> kalau kamu lebih suka untung kecil tapi lebih sering kena — target lebih dekat di {fmtUsd(rec.levels_safe.tp1)} dan rugi dipotong lebih cepat di {fmtUsd(rec.levels_safe.sl)}. Menurut sejarah 3 tahun, peluang kena {Math.round(rec.confidence_safe * 100)}%{confidence != null ? ` (vs ${confidence}% mode standar)` : ""}.</span>
          </div>
        )}
      </div>
    )}

    <section className="dashboard-grid" style={{ marginTop: 20 }}>
      <div className="panel">
        <div className="panel-header"><div><div className="panel-kicker">Penjelasan tanpa jargon</div><h2>Kenapa sistem bilang begitu</h2></div><BookOpen size={18} className="muted-icon" /></div>
        <div style={{ display: "grid", gap: 10, color: "#c9c9d1", fontSize: 12, lineHeight: 1.65 }}>
          <p style={{ margin: 0 }}>{marketExplain} {rsiExplain}</p>
          {status === "entry" && rec.pattern && <p style={{ margin: 0 }}>Pada grafik 1 jam, terbentuk pola <b>{PATTERN_NAMES[rec.pattern] ?? rec.pattern}</b> yang searah dengan tren tersebut — ini pemicu sistem memberi sinyal.</p>}
          {verdict && <p style={{ margin: 0 }}><b>Tentang angka {confidence}%:</b> {verdict.text}</p>}
        </div>
        {(rec.rationale ?? []).length > 0 && (
          <div className="reasoning-ledger" style={{ marginTop: 14 }}>
            <div className="ledger-heading"><span>Catatan teknis (apa adanya)</span><span>from engine</span></div>
            {rec.rationale!.map((line, i) => (
              <div className="ledger-row" key={i}>
                <Check size={15} className="ledger-good" />
                <div><b>{line}</b><span>{rec.created_at_wib ?? ""}</span></div>
                <StatusPill tone={line.includes("JEDA") ? "amber" : "slate"}>{line.includes("JEDA") ? "jeda" : "fakta"}</StatusPill>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="panel">
        <div className="panel-header"><div><div className="panel-kicker">Yang bisa mengubah segalanya</div><h2>Waspadai ini</h2></div><AlertTriangle size={18} className="amber-icon" /></div>
        {nextEvents.length > 0 ? (
          <div className="signal-stack">
            {nextEvents.map(({ e, t }) => (
              <div className="stack-row" key={`${e.title}-${e.t_utc}`}>
                <div className="stack-icon amber"><CalendarDays size={17} /></div>
                <div><b>{e.title}</b><small>Rilis {formatWib(t)} — {t > now ? countdown(t - now) : "sudah rilis"}. Saat ini harga bisa bergerak liar.</small></div>
                <span className="cal-countdown" style={{ color: "#f0b429", fontSize: 9, fontWeight: 700 }}>{e.actual ? `aktual ${e.actual}` : "3★"}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-feed" style={{ padding: "18px 0" }}>
            <div className="empty-icon"><CalendarDays size={20} /></div>
            <h3>Tidak ada rilis besar dalam waktu dekat</h3>
            <p>Bagus — tidak ada jendela risiko event 3★ US mendekat.</p>
          </div>
        )}
        {news.length > 0 && (
          <div className="source-list" style={{ marginTop: 16 }}>
            {news.map((n, i) => (
              <a className="source-row" href={n.url} target="_blank" rel="noreferrer" key={i}>
                <div className="source-logo"><Newspaper size={15} /></div>
                <div><b>{n.title}</b><small>{n.source}</small></div>
                <ExternalLink size={14} />
              </a>
            ))}
          </div>
        )}
        <div className="risk-note" style={{ marginTop: 14 }}>
          <ShieldAlert size={15} /><span>Ringkasan ini <b>bukan saran beli/jual</b>. Semua angka adalah statistik dari data historis — masa depan tidak menjamin mengulanginya.</span>
        </div>
      </div>
    </section>
  </>);
}

// ---- view: Overview ----

function Overview({ data, spot, now, onNavigate }: { data: DashboardData; spot: Spot | null; now: number; onNavigate: (v: ViewKey) => void }) {
  const rec = data.recommendation ?? {};
  const status = rec.status ?? "netral";
  const bias = rec.bias ?? "netral";
  const lv = rec.levels ?? null;
  const bars4h = data.bars4h;
  const lastBar = bars4h[bars4h.length - 1];
  const prevBar = bars4h[bars4h.length - 2];
  const livePrice = spot?.price ?? lastBar?.c;
  const spotChange = livePrice != null && prevBar ? pctChange(livePrice, prevBar.c) : null;
  const trend = rec.h4_context?.trend ?? "sideways";
  const rsi = rec.h4_context?.rsi14 ?? 50;
  const blk = rec.blackout ?? (data.calendar?.events ? activeBlackout(data.calendar.events, now) : null);
  const nextEv = rec.next_event ?? null;

  const headline =
    status === "tunggu" ? <>Gold waits for <span>the event.</span></> :
    bias === "bullish" ? <>Gold is holding <span>above structure.</span></> :
    bias === "bearish" ? <>Gold is pressing <span>below structure.</span></> :
    <>Gold is coiling <span>inside structure.</span></>;

  const confidence = rec.confidence != null ? Math.round(rec.confidence * 100) : null;
  const trendScore = scoreScenario({
    trendAligned: trend === "up",
    momentumSupportive: rsi >= 45 && rsi <= 70,
    macroRisk: blk ? "high" : nextEv ? "medium" : "low",
  });

  const cov1h = data.meta?.coverage?.["1h"];
  const daysRetained = cov1h?.from && cov1h?.to
    ? Math.round((utcStringToTs(cov1h.to) - utcStringToTs(cov1h.from)) / 86_400_000)
    : null;

  const entryZone = lv ? `${fmtUsd(lv.entry - lv.atr14 * 0.5, 0)} — ${fmtUsd(lv.entry + lv.atr14 * 0.5, 0)}` : null;
  const signalCopy =
    status === "tunggu" && blk
      ? <>Event bintang-3 <strong>{blk.title}</strong> dalam jendela rilis — sistem menahan rekomendasi entry sampai jeda berlalu. Cek panel Fundamentals untuk jadwal.</>
      : status === "entry" && lv
        ? <>Setup aktif hari ini: bias <strong>{bias}</strong> dari pola H1 searah trend H4. Zona menarik di <strong>{entryZone}</strong>, invalidasi <strong>{fmtUsd(lv.sl)}</strong>.</>
        : <>Tidak ada setup memenuhi aturan hari ini — flat adalah posisi yang valid. Sistem akan entry saat pola H1 muncul searah trend H4 dan tidak ada jeda event.</>;

  return (<>
    <section className="hero-row">
      <div>
        <div className="eyebrow"><span className="live-dot" />Market overview <span className="eyebrow-separator">/</span> {new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", weekday: "long", day: "2-digit", month: "short", year: "numeric" }).format(now)}</div>
        <h1>{headline}</h1>
        <p className="hero-subtitle">Pandangan berbasis aturan atas XAUUSD — dibangun untuk review yang berulang, bukan teater prediksi.</p>
      </div>
      <div className="hero-actions">
        <button className="icon-button" aria-label="Refresh data" onClick={() => window.location.reload()}><RefreshCw size={17} /></button>
        <button className="primary-button" onClick={() => onNavigate("summary")}><Sparkles size={16} />Open daily analysis</button>
      </div>
    </section>

    <div className="signal-banner">
      <div className="signal-mark"><TrendingUp size={20} /></div>
      <div className="signal-copy">
        <div className="signal-title">
          {status === "tunggu" ? <>Event hold <StatusPill tone="amber">Jeda entry</StatusPill></>
            : status === "entry" ? <>4H structure bias <StatusPill tone={bias === "bearish" ? "amber" : "green"}>{bias === "bearish" ? "Deteriorating" : "Constructive"}</StatusPill></>
            : <>4H structure bias <StatusPill tone="slate">{trend === "up" ? "Constructive" : trend === "down" ? "Deteriorating" : "Neutral"}</StatusPill></>}
        </div>
        <p>{signalCopy}</p>
      </div>
      <div className="signal-score"><span>Confidence band</span><strong>{confidence != null ? confidence : "—"}{confidence != null && <span>%</span>}</strong></div>
    </div>

    <section className="metric-grid">
      <MetricCard label="XAUUSD spot" value={livePrice != null ? fmtUsd(livePrice) : "—"}
        change={spotChange != null ? `${spotChange >= 0 ? "+" : ""}${spotChange.toFixed(2)}%` : undefined}
        icon={Activity} footnote={spot ? `Live · XAUS.com · ${formatWib(spot.at, false)}` : "Close tersimpan (live offline)"} />
      <MetricCard label="4H trend score" value={`${trendScore} / 100`} icon={Gauge} tone="lime" footnote={`Struktur ${trend} · RSI ${rsi.toFixed(0)}`} />
      <MetricCard label="Volatility (ATR)" value={lv ? fmtUsd(lv.atr14) : "—"} icon={Zap} tone="amber" footnote="14-period · H1, dasar SL/TP" />
      <MetricCard label="History retained" value={daysRetained != null ? `${daysRetained.toLocaleString("en-US")} days` : "—"} icon={Database} tone="slate" footnote={`${(cov1h?.bars ?? 0).toLocaleString("en-US")} bar H1 tersimpan`} />
    </section>

    <section className="dashboard-grid">
      <div className="panel chart-panel">
        <div className="panel-header">
          <div>
            <div className="panel-kicker">Primary instrument <span className="verified-icon"><Check size={11} /></span></div>
            <div className="panel-title-row">
              <h2>XAUUSD <span>· Spot gold</span></h2>
              {spotChange != null && (
                <span className={`price-change ${spotChange >= 0 ? "positive" : "negative"}`}>
                  {(livePrice ?? 0) - (prevBar?.c ?? 0) >= 0 ? "+" : ""}{((livePrice ?? 0) - (prevBar?.c ?? 0)).toFixed(2)} <small>({spotChange >= 0 ? "+" : ""}{spotChange.toFixed(2)}%)</small>
                </span>
              )}
            </div>
          </div>
        </div>
        <PriceChart data={data} spot={spot} />
      </div>
      <div className="panel posture-panel">
        <div className="panel-header">
          <div><div className="panel-kicker">Daily posture</div><h2>Decision map</h2></div>
          <div style={{ textAlign: "right" }}>
            <button className="ghost-button" onClick={() => onNavigate("analysis")}>Details <ArrowUpRight size={14} /></button>
            <div className="panel-kicker" style={{ marginTop: 8, fontSize: 8, color: "#666674", fontWeight: 500 }}>
              <Clock3 size={10} style={{ verticalAlign: "-1px" }} /> Update {rec.created_at_wib ?? data.meta?.updated_at_wib ?? "—"}
            </div>
          </div>
        </div>
        <div className="posture-gauge">
          <div className="gauge-ring" style={{ background: `conic-gradient(#d5ff3f 0 ${confidence ?? 0}%, #35353e ${confidence ?? 0}% 100%)` }}>
            <div><strong>{confidence != null ? `${confidence}%` : "—"}</strong><span>{bias}<br />probability</span></div>
          </div>
          <div className="gauge-copy">
            <StatusPill tone={status === "entry" ? "green" : status === "tunggu" ? "amber" : "slate"}>
              {status === "entry" ? "Trend-aligned" : status === "tunggu" ? "Event gate" : "No setup"}
            </StatusPill>
            <p>{status === "entry" && lv ? "Favour pullback entries while price holds the invalidation line." : status === "tunggu" ? "Tunggu jendela rilis berlalu sebelum eksposur baru." : "Tidak ada entry hari ini — review besok."}</p>
          </div>
        </div>
        <div className="zone-list">
          <div className="zone-row"><span className="zone-bar lime" /><div><b>Preferred entry</b><small>{entryZone ?? "— belum ada zona aktif"}</small></div>{lv && <span className="zone-tag lime">watch</span>}</div>
          <div className="zone-row"><span className="zone-bar violet" /><div><b>{bias === "bearish" ? "Breakdown" : "Breakout"} trigger</b><small>{lv ? `Close ${bias === "bearish" ? "di bawah" : "di atas"} ${fmtUsd(lv.tp1)}` : "—"}</small></div>{lv && <span className="zone-tag violet">confirm</span>}</div>
          <div className="zone-row"><span className="zone-bar red" /><div><b>Invalidation</b><small>{lv ? `H1 close ${bias === "bearish" ? "di atas" : "di bawah"} ${fmtUsd(lv.sl)}` : "—"}</small></div>{lv && <span className="zone-tag red">risk</span>}</div>
        </div>
        <div className="risk-note"><ShieldAlert size={15} /><span>Catatan risiko: zona bersifat probabilistik, bukan jaminan. Ukuran posisi dari jarak invalidasi.</span></div>
      </div>
    </section>

    <section className="lower-grid">
      <div className="panel structure-panel">
        <div className="panel-header"><div><div className="panel-kicker">Market structure</div><h2>Signal stack</h2></div><StatusPill tone="violet">4H first</StatusPill></div>
        <div className="signal-stack">
          <div className="stack-row"><div className="stack-icon lime"><TrendingUp size={17} /></div><div><b>EMA alignment</b><small>Posisi EMA20 vs EMA50 pada H4</small></div><span className={`stack-state ${trend === "up" ? "good" : trend === "down" ? "caution" : ""}`}>{trend === "up" ? "Aligned" : trend === "down" ? "Broken" : "Mixed"}</span></div>
          <div className="stack-row"><div className="stack-icon violet"><Activity size={17} /></div><div><b>Momentum (RSI 14)</b><small>H4 RSI {rsi.toFixed(1)} — {rsi > 70 ? "overextended" : rsi < 45 ? "lemah" : "positif, belum overextended"}</small></div><span className={`stack-state ${rsi > 45 && rsi <= 70 ? "good" : "caution"}`}>{rsi > 70 ? "Overextended" : rsi >= 45 && rsi <= 70 ? "Supportive" : "Weak"}</span></div>
          <div className="stack-row"><div className="stack-icon amber"><AlertTriangle size={17} /></div><div><b>Macro event risk</b><small>{blk ? `${blk.title} dalam jendela jeda` : nextEv ? `${nextEv.title} terdekat — cek sebelum eksposur` : "Tidak ada event bintang-3 US dalam 24 jam"}</small></div><span className={`stack-state ${blk || nextEv ? "caution" : "good"}`}>{blk ? "Hold" : nextEv ? "Check" : "Clear"}</span></div>
        </div>
        <button className="full-link" onClick={() => onNavigate("analysis")}>View reasoning ledger <ArrowUpRight size={14} /></button>
      </div>
      <RuleMonitor data={data} onNavigate={onNavigate} />
    </section>
  </>);
}

// panel "Recent review" — feedback loop nyata dari tracking.json
function RuleMonitor({ data, onNavigate }: { data: DashboardData; onNavigate: (v: ViewKey) => void }) {
  const stats = data.tracking?.stats;
  const history = data.tracking?.history ?? [];
  const eodDays = (data.tracking?.eod ?? []).slice(0, 3); // 3 hari terakhir
  const resolved = stats?.resolved ?? 0;
  const hitRate = stats?.hit_rate != null ? Math.round(stats.hit_rate * 100) : null;

  // cumulative outcome trail: win +1.5R, loss −1R, timeout 0
  const cum: number[] = [];
  let run = 0;
  for (const h of history) {
    if (h.status === "win") run += 1.5;
    else if (h.status === "loss") run -= 1;
    cum.push(Number(run.toFixed(2)));
  }

  return (
    <div className="panel performance-panel">
      <div className="panel-header"><div><div className="panel-kicker">Rule monitor · feedback loop</div><h2>Recent review</h2></div><button className="ghost-button" onClick={() => onNavigate("backtest")}>Open lab <ArrowUpRight size={14} /></button></div>
      <div className="performance-top">
        <div>
          <span>Rekomendasi teresolve: {resolved}</span>
          <strong>{hitRate != null ? `${hitRate}%` : "—"}<small style={{ fontSize: 11, color: "#8e8e9b" }}> hit TP1</small></strong>
          <small>{stats?.total ?? 0} rekomendasi tercatat total</small>
        </div>
        {cum.length > 1 ? <MiniSparkline points={cum} /> : <div className="mini-spark" />}
      </div>
      <div className="performance-stats">
        <div><span>Win rate</span><b>{hitRate != null ? `${hitRate}%` : "—"}</b></div>
        <div><span>SL / timeout</span><b>{stats?.losses ?? 0} / {stats?.timeouts ?? 0}</b></div>
        <div><span>Berjalan</span><b>{stats?.active ?? 0}</b></div>
      </div>
      {eodDays.length > 0 && (
        <div className="reasoning-ledger" style={{ marginTop: 4 }}>
          <div className="ledger-heading"><span>Evaluasi akhir hari — mengapa benar/salah</span><span>log sistem</span></div>
          {eodDays.map((d) => (d.entries ?? []).map((e, i) => (
            <div className="ledger-row" key={`${d.date}-${i}`}>
              {e.status === "win" ? <Check size={15} className="ledger-good" />
                : e.status === "loss" ? <AlertTriangle size={15} className="ledger-caution" />
                  : <Clock3 size={15} className="ledger-good" />}
              <div>
                <b>{d.date}{e.pattern ? ` · ${PATTERN_NAMES[e.pattern] ?? e.pattern}` : ""} — {e.status === "win" ? "benar" : e.status === "loss" ? "salah" : "tidak terbukti"}</b>
                <span>{e.why ?? "menunggu narasi hasil"}</span>
              </div>
              <StatusPill tone={e.status === "win" ? "green" : e.status === "loss" ? "amber" : "slate"}>
                {e.status === "win" ? "TP1" : e.status === "loss" ? "SL" : "timeout"}
              </StatusPill>
            </div>
          )))}
        </div>
      )}
      <div className="disclaimer-row"><FileCheck2 size={14} /> Dinilai dari harga aktual · probabilitas, bukan jaminan</div>
    </div>
  );
}

// ---- view: Daily analysis (recommendation.json) ----

function AnalysisView({ data, now }: { data: DashboardData; now: number }) {
  const [showMethodology, setShowMethodology] = useState(false);
  const [note, setNote] = useState(() => {
    try { return localStorage.getItem("goldpulse-note") ?? ""; } catch { return ""; }
  });
  const [saved, setSaved] = useState(false);
  const rec = data.recommendation ?? {};
  const status = rec.status ?? "netral";
  const bias = rec.bias ?? "netral";
  const lv = rec.levels ?? null;
  const confidence = rec.confidence != null ? Math.round(rec.confidence * 100) : null;
  const rationale = rec.rationale ?? [];
  const patternName = rec.pattern ? (PATTERN_NAMES[rec.pattern] ?? rec.pattern) : null;
  const lvSafe = rec.levels_safe ?? null;
  const safeConf = rec.confidence_safe != null ? Math.round(rec.confidence_safe * 100) : null;

  const scenarioTitle =
    status === "tunggu" ? "Event hold" :
    bias === "bullish" ? "Pullback continuation" :
    bias === "bearish" ? "Rejection continuation" : "No qualified setup";

  const lead = status === "entry" && lv
    ? `Bias ${bias} di H1${patternName ? ` dari pola ${patternName}` : ""}, searah konteks H4 (${rec.h4_context?.trend ?? "—"}). Setup terbersih adalah menunggu harga ke zona entry, lalu konfirmasi respons bullish/bearish sebelum eksekusi.`
    : status === "tunggu"
      ? `Event bintang-3 (${rec.blackout?.title ?? "US data"}) sedang dalam jendela rilis. Sistem menahan semua rekomendasi entry sampai jeda berlalu — jangan mengejar pergerakan saat rilis.`
      : `Tidak ada pola H1 dalam 2 bar terakhir yang searah trend H4. Hari ini tidak ada basis entry — flat adalah keputusan yang valid.`;

  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><Crosshair size={13} /> Analysis workspace <span className="eyebrow-separator">/</span> {rec.created_at_wib ?? "Daily review"}</div>
        <h1>Map the trade. <span>Respect the line.</span></h1>
        <p className="hero-subtitle">Pembacaan terstruktur atas price action, konteks, dan apa yang akan membantalkan pembacaan itu.</p>
      </div>
      <button className="secondary-button" onClick={() => setShowMethodology(!showMethodology)}><BookOpen size={16} />Methodology</button>
    </section>
    {showMethodology && (
      <div className="methodology-card">
        <div className="methodology-icon"><BookOpen size={17} /></div>
        <div><b>Kebijakan analisa transparan</b><p>Setiap view memisahkan struktur harga yang teramati, indikator turunan, event eksternal, dan interpretasi. Angka confidence adalah probabilitas historis pola (win-rate out-of-sample), bukan janji akurasi. Rekomendasi dinilai ulang setiap hari oleh feedback loop — hasil terbuka di panel Rule monitor.</p></div>
        <button onClick={() => setShowMethodology(false)} aria-label="Close methodology"><X size={16} /></button>
      </div>
    )}
    <div className="analysis-layout">
      <div className="analysis-main">
        <div className="panel analysis-card">
          <div className="analysis-card-top">
            <div><div className="panel-kicker">Scenario 01 · Base case</div><h2>{scenarioTitle}</h2></div>
            <div className="scenario-probability"><strong>{confidence != null ? `${confidence}%` : "—"}</strong><span>scenario weight</span></div>
          </div>
          <p className="analysis-lead">{lead}{rec.confidence_note ? ` ${rec.confidence_note}.` : ""}</p>
          <div className="level-grid">
            <div className="level-card entry"><span>Entry zone</span><strong>{lv ? `${fmtUsd(lv.entry - lv.atr14 * 0.5, 0)} — ${fmtUsd(lv.entry + lv.atr14 * 0.5, 0)}` : "—"}</strong><small>{lv ? "Tunggu respons di zona; jangan kejar" : "Tidak ada level aktif hari ini"}</small></div>
            <div className="level-card target"><span>First objective</span><strong>{lv ? fmtUsd(lv.tp1) : "—"}</strong><small>TP1 · 1,5× ATR dari entry</small></div>
            <div className="level-card invalidation"><span>Invalidation</span><strong>{lv ? fmtUsd(lv.sl) : "—"}</strong><small>SL · 1× ATR; close {bias === "bearish" ? "di atas" : "di bawah"} batalkan setup</small></div>
          </div>
          {lvSafe && (
            <div className="risk-note" style={{ marginTop: 14 }}>
              <ShieldAlert size={15} /><span><b>Mode aman (untuk "beberapa pips asal aman"):</b> TP1 {fmtUsd(lvSafe.tp1)} · SL {fmtUsd(lvSafe.sl)} — target 1× ATR (lebih dekat), SL 0,75× ATR (lebih ketat), entry sama. Peluang historis {safeConf != null ? `${safeConf}%` : "—"}{confidence != null ? ` vs ${confidence}% standar` : ""} — dinilai dari backtest 3 tahun dengan rule terpisah; feedback loop harian masih menilai level standar.</span>
            </div>
          )}
          <div className="reasoning-ledger">
            <div className="ledger-heading"><span>Evidence ledger</span><span>Observed → interpreted</span></div>
            {rationale.length ? rationale.map((line, i) => (
              <div className="ledger-row" key={i}>
                {line.includes("JEDA") ? <AlertTriangle size={15} className="ledger-caution" /> : <Check size={15} className="ledger-good" />}
                <div><b>{line}</b><span>dari job analisa harian · {rec.created_at_wib ?? ""}</span></div>
                <StatusPill tone={line.includes("JEDA") ? "amber" : "slate"}>{line.includes("JEDA") ? "gating" : "observed"}</StatusPill>
              </div>
            )) : (
              <div className="ledger-row"><Check size={15} className="ledger-good" /><div><b>Menunggu job analisa harian pertama</b><span>recommendation.json belum tersedia</span></div><StatusPill tone="slate">pending</StatusPill></div>
            )}
          </div>
        </div>
        <div className="panel invalidation-panel">
          <div className="panel-header"><div><div className="panel-kicker">What changes the view</div><h2>Invalidation map</h2></div><AlertTriangle size={18} className="amber-icon" /></div>
          <div className="invalidation-list">
            <div><span className="number-badge">01</span><p><b>{lv ? `H1 close ${bias === "bearish" ? "di atas" : "di bawah"} ${fmtUsd(lv.sl)}` : "Setup batal bila struktur berbalik"}</b><br /><small>Menghapus dasar pola dan menurunkan bobot skenario.</small></p></div>
            <div><span className="number-badge">02</span><p><b>{rec.next_event ? `Kejutan data: ${rec.next_event.title}` : "Kejutan data high-impact"}</b><br /><small>Re-score asumsi volatilitas di sekitar rilis; jeda entry −2h..+1h otomatis.</small></p></div>
            <div><span className="number-badge">03</span><p><b>Wick tanpa konfirmasi close</b><br /><small>Jangan perlakukan wick sebagai konfirmasi; butuh close dan follow-through.</small></p></div>
          </div>
        </div>
      </div>
      <aside className="analysis-side">
        <div className="panel side-panel">
          <div className="panel-kicker">Risk protocol</div>
          <h2>Before you act</h2>
          <div className="checklist">
            <div><Check size={14} />Definisikan rugi sebelum entry</div>
            <div><Check size={14} />Cek spread dan sesi pasar</div>
            <div><Check size={14} />Verifikasi kalender makro</div>
            <div><Check size={14} />Satu skenario pada satu waktu</div>
          </div>
          <div className="risk-meter">
            <div className="risk-meter-top"><span>Risk environment</span><b>{status === "tunggu" ? "Elevated" : "Moderate"}</b></div>
            <div className="meter-track"><span style={{ width: status === "tunggu" ? "78%" : "57%" }} /></div>
            <small>{status === "tunggu" ? "Jendela rilis bintang-3 aktif — tunda eksposur baru." : "ATR hari ini jadi dasar jarak SL/TP."}</small>
          </div>
        </div>
        <div className="panel side-panel">
          <div className="panel-kicker">Analyst notes</div>
          <h2>Record the why</h2>
          <textarea placeholder="Tambahkan observasi untuk review harian ini…" value={note} onChange={(e) => { setNote(e.target.value); setSaved(false); }} />
          <button className="secondary-button full" onClick={() => { try { localStorage.setItem("goldpulse-note", note); } catch { /* private mode */ } setSaved(true); }}>{saved ? "Tersimpan ✓" : "Save note"}</button>
          <small style={{ color: "#666674", fontSize: 9, display: "block", marginTop: 8 }}>Disimpan lokal di perangkat ini (bukan di server).</small>
        </div>
      </aside>
    </div>
  </>);
}

// ---- view: Backtest lab (patterns.json + tracking.json) ----

// iOS Safari tidak merender PDF di dalam <iframe> (tampil kosong) — di
// iPhone/iPad tampilkan kartu "buka tab baru" sebagai gantinya.
const IOS_PDF_BLOCKED =
  typeof navigator !== "undefined" &&
  (/iP(hone|ad|od)/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" &&
      (navigator.maxTouchPoints ?? 0) > 1)); // iPadOS 13+ "macOS mode"

// iOS Safari/iPadOS tidak merender PDF dalam iframe — render per halaman ke
// kanvas via pdf.js. Di-import lazy supaya bundle utama tetap ringan
// (chunk pdf.js hanya diunduh di perangkat yang memang membutuhkannya).
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

function PdfPages({ url }: { url: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
        const doc = await pdfjs.getDocument({ url }).promise;
        const wrap = wrapRef.current;
        if (!wrap || !alive) return;
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        for (let i = 1; i <= doc.numPages; i++) {
          if (!alive) return;
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const width = wrap.clientWidth || 300;
          const viewport = page.getViewport({ scale: (width / base.width) * dpr });
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width);
          canvas.height = Math.floor(viewport.height);
          canvas.style.width = "100%";
          canvas.style.display = "block";
          const ctx = canvas.getContext("2d");
          if (!ctx) throw new Error("canvas 2d tidak tersedia");
          await page.render({ canvasContext: ctx, viewport }).promise;
          wrap.appendChild(canvas);
        }
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => { alive = false; };
  }, [url]);
  if (failed) {
    return (
      <div className="pdf-frame-box pdf-ios-fallback">
        <FileText size={24} />
        <p>Laporan gagal dirender di halaman.</p>
        <a className="secondary-button" href={url} target="_blank" rel="noreferrer">
          Buka laporan <ExternalLink size={13} />
        </a>
      </div>
    );
  }
  return <div className="pdf-frame-box pdf-js-box" ref={wrapRef} />;
}

function PdfFrame({ name, title, v }: { name: string; title: string; v?: number }) {
  const url = reportUrl(name, v);
  if (!IOS_PDF_BLOCKED) {
    return (
      <div className="pdf-frame-box">
        <iframe src={url} title={title} />
      </div>
    );
  }
  return <PdfPages url={url} />;
}

function BacktestView({ data }: { data: DashboardData }) {
  const [tf, setTf] = useState<"4h" | "1h">("4h");
  const [selected, setSelected] = useState<string | null>(null);
  const [reports, setReports] = useState<ReportsIndex | null>(null);
  const [openPdf, setOpenPdf] = useState<ReportFile | null>(null);
  useEffect(() => { loadReports().then(setReports); }, []);
  const results = useMemo(() =>
    (data.patterns?.results ?? []).filter((r) => r.tf === tf && (r.n ?? 0) >= 10),
    [data.patterns, tf]);
  const current = useMemo(() =>
    results.find((r) => r.pattern === selected) ?? results[0] ?? null,
    [results, selected]);
  const params = data.patterns?.params as { tp_atr?: number; sl_atr?: number; horizon?: Record<string, number> } | undefined;
  const cov = data.meta?.coverage?.["1h"];

  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><FlaskConical size={13} /> Backtest lab <span className="eyebrow-separator">/</span> Historical replay</div>
        <h1>Test the rules. <span>Keep the caveats.</span></h1>
        <p className="hero-subtitle">Statistik nyata dari engine backtest: sinyal pola searah trend, TP 1,5×ATR vs SL 1×ATR, aturan konservatif, uji out-of-sample 70/30.</p>
      </div>
      <StatusPill tone="violet"><Database size={12} />{cov ? `${cov.bars?.toLocaleString("en-US")} bar H1` : "—"}</StatusPill>
    </section>
    <div className="backtest-layout">
      <div className="panel controls-panel">
        <div className="panel-header"><div><div className="panel-kicker">Konfigurasi (parameter tersimpan)</div><h2>Replay assumptions</h2></div><SlidersHorizontal size={18} className="muted-icon" /></div>
        <label>Pola (rule set)<select value={current?.pattern ?? ""} onChange={(e) => setSelected(e.target.value)}>
          {results.length ? results.map((r) => (
            <option key={r.pattern} value={r.pattern}>{PATTERN_NAMES[r.pattern] ?? r.pattern}</option>
          )) : <option value="">belum ada pola (n ≥ 10)</option>}
        </select></label>
        <label>Timeframe<select value={tf} onChange={(e) => { setTf(e.target.value as "4h" | "1h"); setSelected(null); }}>
          <option value="4h">H4 · primary</option>
          <option value="1h">H1 · secondary</option>
        </select></label>
        <label>Lookback<input value={cov?.from ? `${cov.from.slice(0, 10)} — ${cov.to?.slice(0, 10)}` : "—"} readOnly /></label>
        <label>Parameter tersimpan<div className="range-row"><input type="range" min={10} max={35} value={((params?.sl_atr ?? 1) * 10).toFixed(0)} readOnly /><b>SL {(params?.sl_atr ?? 1).toFixed(1)}× · TP {(params?.tp_atr ?? 1.5).toFixed(1)}× ATR</b></div></label>
        <div className="assumption-box">
          <div><CircleHelp size={14} /><b>Definisi aturan</b></div>
          <p>Entry saat pola muncul di bar H1/H4 searah trend EMA. SL {params?.sl_atr ?? 1}×ATR, TP {params?.tp_atr ?? 1.5}×ATR dalam horizon {params?.horizon?.[tf] ?? "—"} bar. TP+SL di bar sama dihitung LOSS (konservatif). Statistik di-refresh tiap pagi oleh GitHub Actions.</p>
        </div>
        <div style={{ fontSize: 9, color: "#666674", lineHeight: 1.5 }}>Engine berjalan serverless (schedule harian) — panel ini menampilkan hasil tersimpan terbaru, bukan simulasi lokal.</div>
      </div>
      <div className="backtest-results">
        <div className="result-strip">
          <div><span>Avg. R per trade</span><strong>{current?.avg_r != null ? `${current.avg_r >= 0 ? "+" : ""}${current.avg_r.toFixed(2)}R` : "—"}</strong><small>{PATTERN_NAMES[current?.pattern ?? ""] ?? current?.pattern ?? ""} · {tf.toUpperCase()}</small></div>
          <div><span>Win rate</span><strong>{current?.win_rate != null ? `${(current.win_rate * 100).toFixed(1)}%` : "—"}</strong><small>{current?.resolved ?? 0} sinyal teresolve</small></div>
          <div><span>Out-of-sample</span><strong>{current?.oos_win_rate != null ? `${(current.oos_win_rate * 100).toFixed(1)}%` : "—"}</strong><small>30% data terakhir (n={current?.oos_n ?? 0})</small></div>
          <div><span>Sample size</span><strong>{current?.n ?? 0}</strong><small>Total sinyal terdeteksi</small></div>
        </div>
        <div className="panel backtest-chart-panel">
          <div className="panel-header"><div><div className="panel-kicker">Perbandingan pola · {tf.toUpperCase()}</div><h2>Win-rate historis</h2></div><StatusPill tone="green"><Check size={12} />Data riil</StatusPill></div>
          <div className="equity-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={results.map((r) => ({ name: PATTERN_NAMES[r.pattern] ?? r.pattern, win: Number(((r.win_rate ?? 0) * 100).toFixed(1)), oos: Number(((r.oos_win_rate ?? 0) * 100).toFixed(1)) }))} margin={{ top: 8, right: 15, left: -15, bottom: 0 }}>
                <CartesianGrid stroke="#25252f" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "#686873", fontSize: 9 }} axisLine={false} tickLine={false} interval={0} />
                <YAxis domain={[0, 100]} tick={{ fill: "#686873", fontSize: 10 }} axisLine={false} tickLine={false} width={36} tickFormatter={(v: number) => `${v}%`} />
                <Tooltip contentStyle={{ background: "#1c1c23", border: "1px solid #353541", borderRadius: 10, color: "#fff", fontSize: 11 }} cursor={{ fill: "rgba(213,255,63,.05)" }} />
                <Bar dataKey="win" name="In-sample" fill="#d5ff3f" radius={[3, 3, 0, 0]} />
                <Bar dataKey="oos" name="OOS" fill="#8f76ff" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="panel limitations-panel">
          <div className="panel-kicker">Read before interpreting</div>
          <div className="limit-grid">
            <div><AlertTriangle size={15} /><span><b>Historical only</b>Perilaku masa lalu bisa gagal di rezim volatilitas baru.</span></div>
            <div><AlertTriangle size={15} /><span><b>Tanpa friction eksekusi</b>Spread, slippage, dan latensi tidak dimodelkan.</span></div>
            <div><AlertTriangle size={15} /><span><b>Contoh kecil</b>Win-rate pola dengan n kecil bukan jaminan statistik — utamakan yang n≥30.</span></div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div><div className="panel-kicker">Laporan mingguan · otomatis Senin 04:23 WIB</div><h2>Deep-dive PDF</h2></div>
            <StatusPill tone="violet"><BookOpen size={12} />weekly</StatusPill>
          </div>
          <p style={{ fontSize: 11, color: "#8e8e9b", margin: "2px 0 12px" }}>
            Report lengkap tiap pekan: statistik backtest per pola, pembacaan pola berdasarkan history, hasil feedback loop (rekomendasi vs harga aktual), jadwal event 3★ AS, dan sorotan berita tervalidasi.
          </p>
          {(reports?.files ?? []).length ? (<>
            {/* Laporan terbaru langsung terbuka inline — tanpa klik */}
            {reports!.files![0]?.name && (
              <div className="pdf-inline">
                <div className="pdf-modal-head">
                  <b>{reports!.files![0].date_wib ?? reports!.files![0].name}</b>
                  <a className="secondary-button" href={reportUrl(reports!.files![0].name!, reports!.files![0].kb)} target="_blank" rel="noreferrer">Tab baru <ExternalLink size={13} /></a>
                </div>
                <PdfFrame name={reports!.files![0].name!} title="Laporan mingguan GoldPulse" v={reports!.files![0].kb} />
              </div>
            )}
            {reports!.files!.length > 1 && (<>
              <div className="panel-kicker" style={{ margin: "14px 0 2px" }}>Arsip laporan</div>
              <div className="source-list">
                {reports!.files!.slice(1).map((f) => (
                  <button className="source-row" style={{ width: "100%", background: "transparent", border: 0, cursor: "pointer", textAlign: "left", font: "inherit", color: "inherit", padding: "12px 0" }} key={f.name} onClick={() => f.name && setOpenPdf(f)}>
                    <div className="source-logo"><FileCheck2 size={15} /></div>
                    <div><b>{f.date_wib ?? f.name}</b><small>{f.kb != null ? `${f.kb} KB` : ""}{f.summary ? ` · ${f.summary}` : ""} · klik untuk baca</small></div>
                    <ExternalLink size={14} />
                  </button>
                ))}
              </div>
            </>)}
          </>) : (
            <div className="empty-feed" style={{ padding: "14px 0" }}>
              <div className="empty-icon"><BookOpen size={20} /></div>
              <h3>Belum ada laporan</h3>
              <p>Job "Laporan Mingguan" belum pernah menghasilkan PDF — jalankan manual sekali di tab Actions (Laporan Mingguan → Run workflow), atau tunggu jadwal Senin pagi WIB.</p>
            </div>
          )}
          {openPdf?.name && (
            <div className="pdf-modal" role="dialog" aria-modal="true" aria-label="Laporan mingguan PDF">
              <div className="pdf-modal-head">
                <b>{openPdf.date_wib ?? openPdf.name}</b>
                <div className="pdf-modal-actions">
                  <a className="secondary-button" href={reportUrl(openPdf.name, openPdf.kb)} target="_blank" rel="noreferrer">Tab baru <ExternalLink size={13} /></a>
                  <button className="icon-button" aria-label="Tutup laporan" onClick={() => setOpenPdf(null)}><X size={16} /></button>
                </div>
              </div>
              <PdfFrame name={openPdf.name} title="Laporan mingguan GoldPulse" v={openPdf.kb} />
            </div>
          )}
        </div>
      </div>
    </div>
  </>);
}

// ---- view: Riwayat rekomendasi (tracking.json history) ----

const HIST_STATUS: Record<string, { label: string; tone: "green" | "amber" | "violet" | "slate"; icon: typeof Check }> = {
  win: { label: "TP1", tone: "green", icon: Check },
  loss: { label: "SL", tone: "amber", icon: AlertTriangle },
  timeout: { label: "Timeout", tone: "slate", icon: Clock3 },
  active: { label: "Berjalan", tone: "violet", icon: TimerReset },
  entry: { label: "Berjalan", tone: "violet", icon: TimerReset },
};

function RiwayatView({ data }: { data: DashboardData }) {
  const tracking = data.tracking;
  const history = tracking?.history ?? [];
  const [statusFilter, setStatusFilter] = useState("all");
  const [patternFilter, setPatternFilter] = useState("all");

  const patterns = useMemo(
    () => Array.from(new Set(history.map((h) => h.pattern).filter((p): p is string => !!p))),
    [history]);
  const filtered = useMemo(
    () => history.filter((h) => {
      const running = h.status === "active" || h.status === "entry";
      if (statusFilter === "run") { if (!running) return false; }
      else if (statusFilter !== "all" && h.status !== statusFilter) return false;
      return patternFilter === "all" || h.pattern === patternFilter;
    }),
    [history, statusFilter, patternFilter]);

  const stats = tracking?.stats;
  const hitRate = stats?.hit_rate != null ? Math.round(stats.hit_rate * 100) : null;

  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><History size={13} /> Feedback loop <span className="eyebrow-separator">/</span> Histori posisi</div>
        <h1>Setiap rekomendasi. <span>Dinilai jujur.</span></h1>
        <p className="hero-subtitle">Daftar lengkap rekomendasi entry yang pernah dicatat sistem beserta level dan hasilnya vs pergerakan harga aktual — SL dicek lebih dulu (konservatif), timeout 24 bar H1.</p>
      </div>
      <StatusPill tone="violet"><Database size={12} />{tracking?.updated_at_wib ?? "—"}</StatusPill>
    </section>
    {history.length === 0 ? (
      <div className="panel"><div className="empty-feed">
        <div className="empty-icon"><History size={20} /></div>
        <h3>Belum ada riwayat rekomendasi</h3>
        <p>Entry baru tercatat saat job harian menemukan pola H1 searah trend H4 — riwayat terisi otomatis seiring waktu.</p>
      </div></div>
    ) : (<>
      <div className="result-strip">
        <div><span>Total tercatat</span><strong>{stats?.total ?? history.length}</strong><small>rekomendasi entry sejak sistem aktif</small></div>
        <div><span>Hit-rate TP1</span><strong>{hitRate != null ? `${hitRate}%` : "—"}</strong><small>{stats?.wins ?? 0} win / {stats?.losses ?? 0} SL</small></div>
        <div><span>Timeout</span><strong>{stats?.timeouts ?? 0}</strong><small>24 bar H1 tanpa TP/SL</small></div>
        <div><span>Berjalan</span><strong>{stats?.active ?? 0}</strong><small>menunggu hasil</small></div>
      </div>
      <div className="panel">
        <div className="panel-header">
          <div><div className="panel-kicker">Riwayat per rekomendasi</div><h2>{filtered.length} dari {history.length} entry</h2></div>
        </div>
        <div className="filter-bar">
          <label>Hasil
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">Semua</option>
              <option value="win">TP1 (benar)</option>
              <option value="loss">SL (salah)</option>
              <option value="timeout">Timeout</option>
              <option value="run">Masih berjalan</option>
            </select>
          </label>
          <label>Pola
            <select value={patternFilter} onChange={(e) => setPatternFilter(e.target.value)}>
              <option value="all">Semua</option>
              {patterns.map((p) => <option key={p} value={p}>{PATTERN_NAMES[p] ?? p}</option>)}
            </select>
          </label>
        </div>
        <div className="reasoning-ledger">
          <div className="ledger-heading"><span>Tanggal · pola · bias — level, hasil &amp; narasi</span><span>log sistem</span></div>
          {filtered.length === 0 ? (
            <div className="empty-feed" style={{ padding: "14px 0" }}>
              <h3>Tidak ada entry untuk filter ini</h3>
              <p>Coba longgarkan filter hasil atau pola.</p>
            </div>
          ) : filtered.map((h, i) => {
            const meta = HIST_STATUS[h.status ?? ""] ?? { label: h.status ?? "?", tone: "slate" as const, icon: Clock3 };
            const Icon = meta.icon;
            const lvl = h.levels;
            const oc = h.outcome;
            return (
              <div className="ledger-row" key={h.id ?? i}>
                <Icon size={15} className={h.status === "win" ? "ledger-good" : h.status === "loss" ? "ledger-caution" : undefined} />
                <div>
                  <b>{h.created_at_wib?.slice(0, 10) ?? h.id ?? "?"}{h.pattern ? ` · ${PATTERN_NAMES[h.pattern] ?? h.pattern}` : ""} · {h.bias ?? "netral"}</b>
                  <span>{oc?.why ?? (h.status === "active" || h.status === "entry" ? "Masih berjalan — dinilai saat harga sentuh SL/TP1 atau timeout 24 bar H1." : "menunggu narasi hasil")}</span>
                  <div className="hist-detail">
                    {lvl && <span>Entry {fmtUsd(lvl.entry)} · SL {fmtUsd(lvl.sl)} · TP1 {fmtUsd(lvl.tp1)} · TP2 {fmtUsd(lvl.tp2)}</span>}
                    {h.confidence != null && <span>Confidence {Math.round(h.confidence * 100)}%</span>}
                    {oc && <span>Puncak +{(oc.mfe_usd ?? 0).toFixed(2)} / terburuk −{(oc.mae_usd ?? 0).toFixed(2)} USD · {oc.bars_held ?? "—"} jam</span>}
                    {oc?.resolved_at_wib && <span>Resolve {oc.resolved_at_wib}</span>}
                  </div>
                </div>
                <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
              </div>
            );
          })}
        </div>
        <div className="disclaimer-row"><FileCheck2 size={14} /> Dinilai dari harga aktual · SL dicek lebih dulu (konservatif) · probabilitas, bukan jaminan</div>
      </div>
    </>)}
  </>);
}

// ---- view: Fundamentals (calendar.json + news.json) ----

function FundamentalsView({ data, now }: { data: DashboardData; now: number }) {
  const events = data.calendar?.events ?? [];
  const news = data.news?.items ?? [];
  const blk = activeBlackout(events, now);
  const upcoming = useMemo(() =>
    events
      .map((e) => ({ e, t: utcStringToTs(e.t_utc) }))
      .filter(({ t }) => !Number.isNaN(t) && t > now - 24 * 3_600_000)
      .sort((a, b) => a.t - b.t)
      .slice(0, 6),
    [events, now]);

  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><Newspaper size={13} /> Fundamentals <span className="eyebrow-separator">/</span> Verified sources only</div>
        <h1>Context without <span>noise.</span></h1>
        <p className="hero-subtitle">Feed ditolak-dulu: hanya sumber primer/wire terverifikasi dan rilis Trading Economics bintang-3 ke atas, US saja.</p>
      </div>
      <StatusPill tone="slate">{data.calendar?.source === "tradingeconomics" ? "TradingEconomics" : data.calendar?.source === "forexfactory" ? "ForexFactory" : "—"}</StatusPill>
    </section>
    <div className="fundamentals-grid">
      <div className="panel feed-panel">
        <div className="panel-header"><div><div className="panel-kicker">Verification queue</div><h2>High-impact events</h2></div><div className="filter-pill"><span className="status-dot amber" />3★+ only · US</div></div>
        {blk && (
          <div className="risk-note" style={{ marginTop: 12, marginBottom: 6 }}>
            <AlertTriangle size={15} /><span><b>⛔ Jeda entry aktif:</b> {blk.title} — rilis {formatWib(utcStringToTs(blk.t_utc))} ({countdown(utcStringToTs(blk.t_utc) - now)}). Jangan buka posisi baru.</span>
          </div>
        )}
        {upcoming.length ? (
          <div className="signal-stack" style={{ paddingTop: 10 }}>
            {upcoming.map(({ e, t }) => {
              const vals = [
                e.forecast ? `fcst ${e.forecast}` : null,
                e.previous ? `prev ${e.previous}` : null,
                e.actual ? `act ${e.actual}` : null,
              ].filter(Boolean).join(" · ");
              return (
                <div className="stack-row" key={`${e.title}-${e.t_utc}`}>
                  <div className="stack-icon amber"><CalendarDays size={17} /></div>
                  <div><b>{e.title}</b><small>{formatWib(t)} · {vals || "nilai menyusul rilis"}</small></div>
                  <span className="cal-countdown" style={{ color: "#f0b429", fontSize: 9, fontWeight: 700 }}>{t > now ? countdown(t - now) : "selesai"}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="empty-feed">
            <div className="empty-icon"><FileCheck2 size={23} /></div>
            <h3>Belum ada event terdata</h3>
            <p>Jalankan job fundamental (otomatis tiap 4 jam) untuk mengisi feed kalender bintang-3 US.</p>
          </div>
        )}
        <div className="source-policy"><div className="policy-title"><ShieldAlert size={15} />Source policy</div><p>Hanya outlet primer/mapan dan rilis Trading Economics bintang-3+ (US) yang lolos. Opini dan posting sosial tidak masuk. Berita: Google News RSS whitelist + GDELT.</p></div>
      </div>
      <div className="panel sources-panel">
        <div className="panel-kicker">Approved source map</div>
        <h2>Where context comes from</h2>
        <div className="source-list">
          {acceptedSources.map((source) => (
            <a className="source-row" href={source.href} target="_blank" rel="noreferrer" key={source.name}>
              <div className="source-logo"><Globe2 size={17} /></div>
              <div><b>{source.name}</b><small>{source.note}</small></div>
              <ExternalLink size={14} />
            </a>
          ))}
        </div>
        <h2 style={{ fontSize: 14, marginTop: 22 }}>Berita kredibel ({news.length})</h2>
        <div className="source-list">
          {news.length ? news.slice(0, 8).map((n, i) => (
            <a className="source-row" href={n.url} target="_blank" rel="noreferrer" key={i}>
              <div className="source-logo"><Newspaper size={15} /></div>
              <div><b>{n.title}</b><small>{n.source}{n.published_utc ? ` · ${formatWib(utcStringToTs(n.published_utc))}` : ""}</small></div>
              <ExternalLink size={14} />
            </a>
          )) : <small style={{ color: "#666674", fontSize: 10 }}>Belum ada berita dalam 36 jam terakhir.</small>}
        </div>
        <div className="timestamp-card"><Clock3 size={15} /><div><b>All times shown in WIB</b><span>Pipeline check: {data.calendar?.updated_at_wib ?? "—"}</span></div></div>
      </div>
    </div>
  </>);
}

// ---- view: Jadwal (kapan tiap data diperbarui) ----

// spesifikasi jadwal GitHub Actions (WIB) untuk hitung "update berikutnya"
type SchedSpec =
  | { kind: "hourly"; minute: number }                       // tiap jam di menit tertentu
  | { kind: "grid"; minute: number; hours: number[]; tradingDaysOnly?: boolean } // jam grid tertentu
  | { kind: "monday"; hour: number; minute: number };        // Senin sekali sepekan

// Perkiraan jeda (menit) sampai run berikutnya, dihitung dari jam WIB sekarang.
function minutesToNext(now: number, spec: SchedSpec): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const cur = get("hour") * 60 + get("minute");
  const curMin = get("minute"); // "tiap jam di menit N" hanya bandingkan menit
  const dayIdx = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.find((p) => p.type === "weekday")?.value ?? "Mon");

  if (spec.kind === "hourly") return (spec.minute - curMin + 60) % 60 || 60;
  if (spec.kind === "grid") {
    // tradingDaysOnly = hanya Senin–Jumat WIB (jam trading utama emas).
    for (let d = 0; d < 8; d++) {
      if (spec.tradingDaysOnly && (dayIdx + d) % 7 > 4) continue; // lompat Sabtu/Minggu
      for (const h of spec.hours) {
        const s = d * 1440 + h * 60 + spec.minute;
        if (s > cur) return s - cur;
      }
    }
    return 0; // tak terjangkau (grid tanpa tradingDaysOnly selalu ada slot)
  }
  const target = spec.hour * 60 + spec.minute;
  // Senin berikutnya
  if (dayIdx === 0 && cur < target) return target - cur;
  const shift = (8 - dayIdx) % 7 || 7;
  return shift * 1440 + (target - cur);
}

function nextUpdateLabel(now: number, spec: SchedSpec): string {
  const m = minutesToNext(now, spec);
  const h = Math.floor(m / 60), mm = m % 60;
  return h > 0 ? `±${h}j ${mm}m lagi` : `±${mm}m lagi`;
}

const SCHEDULE_ROWS: Array<{
  icon: typeof Clock3; fitur: string; jadwal: string;
  sumber: string; catatan: string; next?: SchedSpec;
}> = [
  { icon: TrendingUp, fitur: "Candle H1/H4", jadwal: "Tiap jam, menit :17 WIB (+ cadangan :47) — semua hari (termasuk Minggu)",
    sumber: "Twelve Data · workflow Hourly Sync",
    catatan: "gap-filling: jam yang terlewat otomatis dikejar di run berikutnya; slot cadangan menjaga data tetap segar kalau GitHub menunda jadwal",
    next: { kind: "hourly", minute: 17 } },
  { icon: Sparkles, fitur: "Rekomendasi harian (entry/tunggu/netral)", jadwal: "Tiap 2 jam saat pasar aktif — 04:37 s.d. 22:37 WIB, Senin–Jumat",
    sumber: "workflow Analisa Harian",
    catatan: "grid 2 jam = tiap pasangan candle H1 dievaluasi tepat sekali (cadangan :52 kalau jadwal tertunda); nempel jam krusial: 04:37 pra-open Sydney, 14:37 open London, 20:37 open NY + rilis data AS; sekalian menilai rekomendasi lama vs harga aktual",
    next: { kind: "grid", minute: 37, hours: [4, 6, 8, 10, 12, 14, 16, 18, 20, 22], tradingDaysOnly: true } },
  { icon: FlaskConical, fitur: "Statistik backtest per pola", jadwal: "Tiap 2 jam (ikut analisa harian)",
    sumber: "workflow Analisa Harian",
    catatan: "walk-forward 70/30, data sampai detik itu",
    next: { kind: "grid", minute: 37, hours: [4, 6, 8, 10, 12, 14, 16, 18, 20, 22], tradingDaysOnly: true } },
  { icon: CalendarDays, fitur: "Kalender ekonomi 3★ US", jadwal: "Tiap 3 jam, menit :13 WIB (+ cadangan :28)",
    sumber: "Trading Economics · workflow Fundamental",
    catatan: "hanya importance 3★, country US; jendela jeda event dihitung dari waktu event di kalender, jadi selalu akurat",
    next: { kind: "grid", minute: 13, hours: [2, 5, 8, 11, 14, 17, 20, 23] } },
  { icon: Newspaper, fitur: "Berita tervalidasi", jadwal: "Tiap 3 jam, menit :13 WIB (job yang sama, cadangan :28)",
    sumber: "Google News RSS + GDELT · workflow Fundamental",
    catatan: "whitelist wire (Reuters, Bloomberg, FT, CNBC dkk), 36 jam terakhir",
    next: { kind: "grid", minute: 13, hours: [2, 5, 8, 11, 14, 17, 20, 23] } },
  { icon: FileCheck2, fitur: "Laporan mingguan PDF", jadwal: "Senin 04:23 WIB",
    sumber: "workflow Laporan Mingguan",
    catatan: "arsip maks 52 laporan, tersimpan di repo",
    next: { kind: "monday", hour: 4, minute: 23 } },
  { icon: Zap, fitur: "Spot live (harga di kartu)", jadwal: "Tiap 30 detik, di browser Anda",
    sumber: "XAUS.com (tanpa API key)",
    catatan: "display only — tidak pernah disimpan ke data" },
  { icon: RefreshCw, fitur: "Refresh dashboard", jadwal: "Tiap 10 menit (browser)",
    sumber: "raw.githubusercontent.com",
    catatan: "tarik-refresh browser selalu mengambil data segar" },
];

function JadwalView({ data, now }: { data: DashboardData; now: number }) {
  const lastRun: Record<string, string | undefined> = {
    "Candle H1/H4": data.meta?.updated_at_wib,
    "Rekomendasi harian (entry/tunggu/netral)": data.recommendation?.created_at_wib,
    "Statistik backtest per pola": data.patterns?.generated_at_wib,
    "Kalender ekonomi 3★ US": data.calendar?.updated_at_wib,
    "Berita tervalidasi": data.news?.updated_at_wib,
    "Laporan mingguan PDF": data.tracking?.updated_at_wib,
    "Spot live (harga di kartu)": "live",
    "Refresh dashboard": "otomatis",
  };
  return (<>
    <section className="hero-row compact">
      <div>
        <div className="eyebrow"><Clock3 size={13} /> Jadwal update <span className="eyebrow-separator">/</span> Semua waktu WIB</div>
        <h1>Kapan data <span>disegarkan.</span></h1>
        <p className="hero-subtitle">Semua update berjalan otomatis di GitHub Actions — tidak ada yang perlu dijalankan manual (kecuali backfill). Angka "terakhir" diambil langsung dari data yang sedang Anda lihat.</p>
      </div>
      <StatusPill tone="green"><Check size={12} />Semua otomatis</StatusPill>
    </section>
    <div className="panel" style={{ padding: 0 }}>
      <div className="panel-header" style={{ padding: "16px 16px 0" }}>
        <div><div className="panel-kicker">Perkiraan update berikutnya</div><h2>Geser untuk lihat semua</h2></div>
      </div>
      <div className="sched-carousel">
        {SCHEDULE_ROWS.map((r) => {
          const Icon = r.icon;
          return (
            <div className="sched-card" key={r.fitur}>
              <div className="sched-fitur"><span className="sched-ico"><Icon size={14} /></span><b>{r.fitur}</b></div>
              <div className="sched-when">{r.jadwal}</div>
              {r.next ? (
                <div className="sched-next"><TimerReset size={12} /> {nextUpdateLabel(now, r.next)}</div>
              ) : (
                <div className="sched-next"><TimerReset size={12} /> berjalan di browser</div>
              )}
              <div className="sched-lab">Terakhir</div>
              <span className="sched-last">{lastRun[r.fitur] ?? "—"}</span>
              <small className="sched-note">{r.catatan}</small>
              <small className="sched-src">{r.sumber}</small>
            </div>
          );
        })}
      </div>
    </div>
    <div className="panel assumption-box" style={{ marginTop: 14 }}>
      <div><CircleHelp size={14} /><b>Kuota API</b></div>
      <p>Twelve Data gratis 800 kredit/hari. Pemakaian sekarang ±48/hari (2 request per sync × 24 jam) — masih sangat longgar. Kalender berita memakai scraping ringan (tanpa kredit).</p>
    </div>
  </>);
}

// ---- shell ----

export default function Home() {
  const { data, spot, loading, now } = useDashboard();
  const wibClock = useWibClock();
  const [view, setView] = useState<ViewKey>("overview");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [noticeHidden, setNoticeHidden] = useState(false);
  const activeLabel = navItems.find((item) => item.key === view)?.label ?? "Overview";
  const rec = data?.recommendation ?? null;
  const status = rec?.status ?? null;

  const content = !data ? (
    <div className="empty-feed"><div className="empty-icon"><Database size={23} /></div><h3>{loading ? "Memuat data pasar…" : "Data belum tersedia"}</h3><p>{loading ? "Mengambil candle, statistik, dan rekomendasi dari repo." : "Pastikan job backfill/sync sudah dijalankan, lalu muat ulang."}</p></div>
  ) : view === "summary" ? (
    <SummaryView data={data} spot={spot} now={now} onNavigate={setView} />
  ) : view === "overview" ? (
    <Overview data={data} spot={spot} now={now} onNavigate={setView} />
  ) : view === "analysis" ? (
    <AnalysisView data={data} now={now} />
  ) : view === "backtest" ? (
    <BacktestView data={data} />
  ) : view === "riwayat" ? (
    <RiwayatView data={data} />
  ) : view === "calendar" ? (
    <FundamentalsView data={data} now={now} />
  ) : (
    <JadwalView data={data} now={now} />
  );

  const days = (() => {
    const cov = data?.meta?.coverage?.["1h"];
    if (!cov?.from || !cov?.to) return 0;
    return Math.round((utcStringToTs(cov.to) - utcStringToTs(cov.from)) / 86_400_000);
  })();

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><span /></div>
          <div><strong>goldpulse</strong><small>XAUUSD intelligence</small></div>
          <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={17} /></button>
        </div>
        <div className="sidebar-section">
          <span className="sidebar-label">Workspace</span>
          <nav>
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.key} className={view === item.key ? "nav-item active" : "nav-item"} onClick={() => { setView(item.key); setMobileOpen(false); }}>
                  <Icon size={17} /><span>{item.label}</span>
                  {item.key === "analysis" && status === "entry" && <span className="nav-badge">1</span>}
                </button>
              );
            })}
          </nav>
        </div>
        <div className="sidebar-bottom">
          <div className="data-health">
            <div className="health-top"><span>Data health</span><StatusPill tone={data?.meta?.status === "ok" ? "green" : "amber"}>{data?.meta?.status ?? "…"}</StatusPill></div>
            <div className="health-bar"><span style={{ width: `${Math.min(100, Math.round((days / 1095) * 100))}%` }} /></div>
            <small>{days ? `${days.toLocaleString("en-US")} hari H1 · candle diperbarui ${data?.meta?.updated_at_wib ?? "—"}` : "menunggu sync pertama"}</small>
          </div>
          <div className="profile">
            <div className="avatar">GP</div>
            <div><b>Growth desk</b><span>Research workspace</span></div>
            <ChevronDown size={14} />
          </div>
        </div>
      </aside>
      {mobileOpen && <button className="mobile-overlay" onClick={() => setMobileOpen(false)} aria-label="Close menu" />}
      <main className="main-content">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
          <div className="mobile-title"><span>Workspace</span><b>{activeLabel}</b></div>
          <div className="topbar-right">
            <div className="wib-chip"><span className="live-dot" />WIB <b>{wibClock}</b></div>
            <button className="icon-button small" aria-label="Help" onClick={() => setView("backtest")}><CircleHelp size={17} /></button>
            <div className="top-avatar">GP</div>
          </div>
        </header>
        <div className="content-wrap">
          {!noticeHidden && (
            <div className="demo-notice">
              <div>
                <AlertTriangle size={14} />
                <span><b>Bukan grafik real-time.</b> Harga live di kartu atas (tiap 30 dtk) hanya tampilan — grafik menampilkan candle final per jam: bar terakhir disinkron tiap jam :17 WIB (terakhir {data?.meta?.updated_at_wib ?? "—"}), rekomendasi dibuat ulang tiap 2 jam saat pasar aktif ({rec?.created_at_wib ?? "—"}). Probabilitas statistik, bukan saran finansial.</span>
              </div>
              <button aria-label="Dismiss notice" onClick={() => setNoticeHidden(true)}><X size={14} /></button>
            </div>
          )}
          {content}
          <footer className="app-footer">
            <span>GoldPulse · research workspace</span>
            <span>All timestamps in WIB (UTC+7) · Not financial advice</span>
          </footer>
        </div>
      </main>
    </div>
  );
}