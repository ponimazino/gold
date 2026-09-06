import { useEffect, useMemo, useState } from "react";
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
  Bell,
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
  FlaskConical,
  Gauge,
  Globe2,
  LayoutDashboard,
  Menu,
  Newspaper,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
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
  PATTERN_NAMES,
  pctChange,
  scoreScenario,
  Spot,
  toChartRows,
  utcStringToTs,
} from "../data";

type ViewKey = "overview" | "analysis" | "backtest" | "calendar";
type Timeframe = "4H" | "1H";

const SOURCE_LINKS = {
  reuters: "https://www.reuters.com/world/",
  tradingEconomics: "https://tradingeconomics.com/calendar",
  twelveData: "https://twelvedata.com/",
  xaus: "https://xaus.com/",
};

const acceptedSources = [
  { name: "Trading Economics", note: "Kalender event bintang-3, US saja", href: SOURCE_LINKS.tradingEconomics },
  { name: "Reuters & wire terverifikasi", note: "Berita breaking — tanpa opini, whitelist sumber", href: SOURCE_LINKS.reuters },
  { name: "Twelve Data + XAUS.com", note: "OHLCV H1/H4 historis + spot live", href: SOURCE_LINKS.twelveData },
];

const navItems: { key: ViewKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "analysis", label: "Daily analysis", icon: Crosshair },
  { key: "backtest", label: "Backtest lab", icon: FlaskConical },
  { key: "calendar", label: "Fundamentals", icon: Newspaper },
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

// ---- chart harga nyata (H4 prioritas / H1) ----

function PriceChart({ data, spot }: { data: DashboardData; spot: Spot | null }) {
  const [timeframe, setTimeframe] = useState<Timeframe>("4H");
  const rows = useMemo(() => {
    const bars = timeframe === "4H" ? data.bars4h : data.bars1h;
    const slice = bars.slice(timeframe === "4H" ? -58 : -120);
    const out = toChartRows(slice);
    // spot live menimpa close candle terakhir (display saja)
    if (spot && out.length) {
      const last = out[out.length - 1];
      last.close = spot.price;
      last.high = Math.max(last.high, spot.price);
      last.low = Math.min(last.low, spot.price);
    }
    return out;
  }, [data, timeframe, spot?.price, spot?.at]);

  const rec = data.recommendation;
  const lv = rec?.levels ?? null;
  const latest = rows[rows.length - 1];
  if (!latest) return <div className="chart-footnote"><span>Data candle belum tersedia.</span></div>;
  const previous = rows[rows.length - 2] ?? latest;
  const min = Math.floor(Math.min(...rows.map((d) => d.low)) - 8);
  const max = Math.ceil(Math.max(...rows.map((d) => d.high)) + 8);
  const labels = rows.filter((_, i) => i % (timeframe === "4H" ? 10 : 20) === 0).map((d) => d.label);
  const delta = pctChange(latest.close, previous.close);

  return (
    <div className="chart-wrap">
      <div className="chart-legend-row">
        <div className="legend-item"><span className="legend-line lime" />Price</div>
        <div className="legend-item"><span className="legend-line purple" />EMA 20</div>
        <div className="legend-item"><span className="legend-line gray" />EMA 50</div>
        <div className="chart-range">{rows[0].label} — {latest.label}</div>
      </div>
      <div className="price-chart">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 18, right: 12, left: -12, bottom: 0 }}>
            <defs>
              <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#d5ff3f" stopOpacity={0.28} /><stop offset="100%" stopColor="#d5ff3f" stopOpacity={0} /></linearGradient>
            </defs>
            <CartesianGrid stroke="#25252f" vertical={false} />
            <XAxis dataKey="label" ticks={labels} tick={{ fill: "#686873", fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis domain={[min, max]} tick={{ fill: "#686873", fontSize: 10 }} axisLine={false} tickLine={false} width={48} tickFormatter={(v: number) => `$${v}`} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#747482", strokeDasharray: "4 4" }} />
            {lv && timeframe === "4H" && (
              <ReferenceArea y1={lv.entry - lv.atr14 * 0.5} y2={lv.entry + lv.atr14 * 0.5} fill="#d5ff3f" fillOpacity={0.08} strokeOpacity={0} />
            )}
            <Area type="monotone" dataKey="close" stroke="#d5ff3f" strokeWidth={2.4} fill="url(#priceFill)" dot={false} activeDot={{ r: 4, fill: "#d5ff3f", stroke: "#16161c", strokeWidth: 2 }} />
            <Line type="monotone" dataKey="ema20" stroke="#a58bff" strokeWidth={1.4} dot={false} />
            <Line type="monotone" dataKey="ema50" stroke="#7a7a86" strokeWidth={1.2} strokeDasharray="5 5" dot={false} />
            <ReferenceLine y={latest.close} stroke="#d5ff3f" strokeDasharray="3 4" strokeOpacity={0.55} label={{ value: fmtUsd(latest.close), position: "insideTopRight", fill: "#d5ff3f", fontSize: 11 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-footnote">
        <span><Clock3 size={13} /> Candle terakhir {formatWib(latest.ts)}</span>
        <span className={latest.close >= previous.close ? "positive" : "negative"}>
          {latest.close >= previous.close ? "▲" : "▼"} {Math.abs(delta).toFixed(2)}% vs candle sebelumnya
        </span>
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
        <button className="primary-button" onClick={() => onNavigate("analysis")}><Sparkles size={16} />Open daily analysis</button>
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
        <div className="panel-header"><div><div className="panel-kicker">Daily posture</div><h2>Decision map</h2></div><button className="ghost-button" onClick={() => onNavigate("analysis")}>Details <ArrowUpRight size={14} /></button></div>
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
      <div className="disclaimer-row"><FileCheck2 size={14} /> Dinilai dari harga aktual · probabilitas, bukan jaminan</div>
    </div>
  );
}

// ---- view: Daily analysis (recommendation.json) ----

function AnalysisView({ data, now }: { data: DashboardData; now: number }) {
  const [showMethodology, setShowMethodology] = useState(false);
  const [note, setNote] = useState(() => localStorage.getItem("goldpulse-note") ?? "");
  const [saved, setSaved] = useState(false);
  const rec = data.recommendation ?? {};
  const status = rec.status ?? "netral";
  const bias = rec.bias ?? "netral";
  const lv = rec.levels ?? null;
  const confidence = rec.confidence != null ? Math.round(rec.confidence * 100) : null;
  const rationale = rec.rationale ?? [];
  const patternName = rec.pattern ? (PATTERN_NAMES[rec.pattern] ?? rec.pattern) : null;

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
          <button className="secondary-button full" onClick={() => { localStorage.setItem("goldpulse-note", note); setSaved(true); }}>{saved ? "Tersimpan ✓" : "Save note"}</button>
          <small style={{ color: "#666674", fontSize: 9, display: "block", marginTop: 8 }}>Disimpan lokal di perangkat ini (bukan di server).</small>
        </div>
      </aside>
    </div>
  </>);
}

// ---- view: Backtest lab (patterns.json + tracking.json) ----

function BacktestView({ data }: { data: DashboardData }) {
  const [tf, setTf] = useState<"4h" | "1h">("4h");
  const [selected, setSelected] = useState<string | null>(null);
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
      </div>
    </div>
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
      .filter(({ t }) => !Number.isNaN(t))
      .sort((a, b) => a.t - b.t)
      .slice(0, 6),
    [events]);

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
        <div className="timestamp-card"><Clock3 size={15} /><div><b>All times shown in WIB</b><span>Pipeline check: {data.calendar?.generated_at_wib ?? "—"}</span></div></div>
      </div>
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
  ) : view === "overview" ? (
    <Overview data={data} spot={spot} now={now} onNavigate={setView} />
  ) : view === "analysis" ? (
    <AnalysisView data={data} now={now} />
  ) : view === "backtest" ? (
    <BacktestView data={data} />
  ) : (
    <FundamentalsView data={data} now={now} />
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
        <div className="sidebar-section">
          <span className="sidebar-label">System</span>
          <button className="nav-item" onClick={() => setView("calendar")}><Bell size={17} /><span>Alerts</span>{data?.recommendation?.next_event && <span className="nav-status" />}</button>
        </div>
        <div className="sidebar-bottom">
          <div className="data-health">
            <div className="health-top"><span>Data health</span><StatusPill tone={data?.meta?.status === "ok" ? "green" : "amber"}>{data?.meta?.status ?? "…"}</StatusPill></div>
            <div className="health-bar"><span style={{ width: `${Math.min(100, Math.round((days / 1095) * 100))}%` }} /></div>
            <small>{days ? `${days.toLocaleString("en-US")} hari H1 · sync ${data?.meta?.updated_at_wib ?? "—"}` : "menunggu sync pertama"}</small>
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
                <span><b>Live data.</b> Sync terakhir {data?.meta?.updated_at_wib ?? "—"} · rekomendasi {rec?.created_at_wib ?? "—"} · probabilitas statistik, bukan saran finansial.</span>
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