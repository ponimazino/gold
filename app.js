// Gold Analisator — chart PWA (vanilla JS, no build step).
// Data: data/xauusd_{1h,4h}.json (Twelve Data, UTC) + live spot dari XAUS.com.
// Semua waktu DITAMPILKAN dalam WIB (Asia/Jakarta); storage tetap UTC.

const API_SPOT = "https://xaus.com/api/v1/spot?compact=1";
const TF_STEP = { "1h": 3600, "4h": 14400 };

const $ = (id) => document.getElementById(id);

// WIB formatters
const fmtDay = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "short" });
const fmtHM = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hour12: false });
const fmtFull = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta", weekday: "short", day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

const state = { tf: "1h", bars: [], cache: {} };

let chart, series;

function initChart() {
  chart = LightweightCharts.createChart($("chart"), {
    layout: {
      background: { color: "#151a21" },
      textColor: "#8b95a3",
      fontFamily: "system-ui, -apple-system, sans-serif",
    },
    grid: {
      vertLines: { color: "#1b212b" },
      horzLines: { color: "#1b212b" },
    },
    rightPriceScale: { borderColor: "#232b36" },
    timeScale: {
      borderColor: "#232b36",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 4,
      // WIB: jam.biasa -> "HH.mm", tengah malam WIB (17:00 UTC) -> tanggal
      tickMarkFormatter: (time) => {
        const d = new Date(time * 1000);
        const hm = fmtHM.format(d);
        return hm === "00.00" || hm === "24.00" ? fmtDay.format(d) : hm;
      },
    },
    localization: {
      locale: "id-ID",
      priceFormatter: (p) => p.toFixed(2),
      timeFormatter: (time) => fmtFull.format(new Date(time * 1000)),
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });

  series = chart.addCandlestickSeries({
    upColor: "#26a69a",
    downColor: "#ef5350",
    wickUpColor: "#26a69a",
    wickDownColor: "#ef5350",
    borderVisible: false,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });

  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: $("chart").clientWidth, height: $("chart").clientHeight });
  });
  ro.observe($("chart"));
  chart.applyOptions({ width: $("chart").clientWidth, height: $("chart").clientHeight });
}

async function loadCandles(tf) {
  if (state.cache[tf]) return state.cache[tf];
  const res = await fetch(`data/xauusd_${tf}.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`data/xauusd_${tf}.json: HTTP ${res.status}`);
  const payload = await res.json();
  const bars = payload.bars.map((b) => ({
    time: Date.parse(b.t) / 1000, // UTC epoch seconds
    open: b.o, high: b.h, low: b.l, close: b.c,
  })).sort((a, b) => a.time - b.time);
  state.cache[tf] = bars;
  return bars;
}

function render(bars) {
  state.bars = bars;
  series.setData(bars);
  chart.timeScale().scrollToRealTime();
  const last = bars[bars.length - 1];
  if (last) updateHeader(last.close, bars[bars.length - 2]?.close, last.time);
}

function updateHeader(price, prevClose, barTime) {
  $("price").textContent = price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (prevClose) {
    const diff = price - prevClose;
    const pct = (diff / prevClose) * 100;
    const el = $("change");
    el.textContent = `${diff >= 0 ? "▲ +" : "▼ "}${diff.toFixed(2)} (${pct.toFixed(2)}%)`;
    el.className = `change ${diff >= 0 ? "up" : "down"}`;
  }
  if (barTime) {
    $("meta-line").textContent =
      `candle terakhir: ${fmtFull.format(new Date(barTime * 1000))} WIB · ${state.bars.length} bar`;
  }
}

function setLive(mode) {
  const el = $("live-state");
  if (mode === "live") { el.textContent = "● live"; el.className = "live-state live"; el.title = "XAUS.com spot"; }
  else { el.textContent = "○ tersimpan"; el.className = "live-state cached"; el.title = "harga live tidak tersedia — menampilkan close tersimpan"; }
}

// Live spot: update candle terakhir (atau buat candle baru kalau ganti jam).
function applyLive(price) {
  const step = TF_STEP[state.tf];
  const last = state.bars[state.bars.length - 1];
  if (!last) return;
  const slot = Math.floor(Date.now() / (step * 1000)) * step;
  const prevClose = state.bars[state.bars.length - 2]?.close;

  if (slot <= last.time) {
    const bar = { ...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price) };
    state.bars[state.bars.length - 1] = bar;
    series.update(bar);
  } else {
    const bar = {
      time: slot, open: last.close, close: price,
      high: Math.max(last.close, price), low: Math.min(last.close, price),
    };
    state.bars.push(bar);
    series.update(bar);
  }
  updateHeader(price, prevClose, slot);
}

async function pollSpot() {
  try {
    const res = await fetch(API_SPOT, { cache: "no-store" });
    const j = await res.json();
    const price = j && j.xau && typeof j.xau.price === "number" ? j.xau.price : null;
    if (price === null) throw new Error("no price field");
    applyLive(price);
    setLive("live");
  } catch {
    setLive("cached"); // harga live gagal -> tetap tampilkan close tersimpan
  }
}

async function switchTf(tf) {
  state.tf = tf;
  document.querySelectorAll(".tf-btn").forEach((b) => b.classList.toggle("active", b.dataset.tf === tf));
  try {
    render(await loadCandles(tf));
  } catch (err) {
    $("meta-line").textContent = `gagal memuat data: ${err.message} — jalankan backfill dulu`;
  }
}

async function loadMeta() {
  try {
    const res = await fetch("data/meta.json", { cache: "no-store" });
    if (!res.ok) return;
    const m = await res.json();
    const el = $("status");
    el.textContent = m.status || "ok";
    el.className = "badge" + (m.status && m.status !== "ok" ? " warn" : "");
    if (m.updated_at_wib) $("sync-time").textContent = `sync terakhir: ${m.updated_at_wib}`;
  } catch { /* meta belum ada — abaikan */ }
}

// ---- Statistik pola (data/patterns.json, hasil job research) ----

const PATTERN_NAMES = {
  bullish_engulfing: "Bullish Engulfing",
  bearish_engulfing: "Bearish Engulfing",
  hammer: "Hammer (pin bar)",
  shooting_star: "Shooting Star",
  inside_bar: "Inside Bar",
};

function winClass(rate) {
  if (rate == null) return "neutral";
  if (rate >= 0.55) return "good";
  if (rate < 0.45) return "bad";
  return "neutral";
}

function renderPatterns(data) {
  const rows = (data.results || [])
    .filter((r) => r.n >= 10)  // sample terlalu kecil tidak ditampilkan
    .map((r) => `
      <div class="pattern-row">
        <span class="tf-chip">${r.tf === "1h" ? "H1" : "H4"}</span>
        <div>
          <div class="p-name">${PATTERN_NAMES[r.pattern] || r.pattern}</div>
          <div class="p-meta">${r.n} sinyal · R rata-rata ${r.avg_r != null ? r.avg_r.toFixed(2) : "—"}</div>
        </div>
        <div>
          <div class="p-win ${winClass(r.win_rate)}">${r.win_rate != null ? (r.win_rate * 100).toFixed(0) + "%" : "—"}</div>
          <div class="p-oos">OOS ${r.oos_win_rate != null ? (r.oos_win_rate * 100).toFixed(0) + "%" : "—"}</div>
        </div>
      </div>`);
  const el = $("patterns");
  if (!rows.length) {
    el.textContent = "belum ada pola dengan sampel cukup (n≥10) — jalankan research setelah backfill";
  } else {
    el.innerHTML = rows.join("");
  }
  $("patterns-sec").hidden = false;
  if (data.generated_at_wib) $("patterns-time").textContent = `Dihitung ${data.generated_at_wib}.`;
}

async function loadPatterns() {
  try {
    const res = await fetch("data/patterns.json", { cache: "no-store" });
    if (!res.ok) return;
    renderPatterns(await res.json());
  } catch { /* patterns.json belum ada — seksi tetap disembunyikan */ }
}

// ---- Rekomendasi harian (data/recommendation.json, hasil job daily) ----

const BIAS_LABEL = { bullish: "BULLISH", bearish: "BEARISH", netral: "NETRAL" };
const STATUS_LABEL = {
  entry: "sinyal entry",
  tunggu: "tunggu — jeda event",
  netral: "tidak ada setup",
};

function renderRecommendation(rec) {
  const card = $("reco-card");
  const bias = rec.bias || "netral";
  const status = rec.status || "netral";
  const statusCls = status === "entry" ? "go" : status === "tunggu" ? "wait" : "flat";

  const levels = rec.levels ? `
    <div class="reco-levels">
      <div><span>Entry</span><b>${rec.levels.entry.toFixed(2)}</b></div>
      <div class="lvl-tp"><span>TP1</span><b>${rec.levels.tp1.toFixed(2)}</b></div>
      <div class="lvl-tp"><span>TP2</span><b>${rec.levels.tp2.toFixed(2)}</b></div>
      <div class="lvl-sl"><span>SL</span><b>${rec.levels.sl.toFixed(2)}</b></div>
    </div>` : "";

  const conf = rec.confidence != null
    ? `<div class="reco-conf">
         <div class="conf-bar"><div class="conf-fill ${winClass(rec.confidence)}"
              style="width:${Math.round(rec.confidence * 100)}%"></div></div>
         <span>confidence ${(rec.confidence * 100).toFixed(0)}%</span>
       </div>` : "";

  card.className = `reco-card ${statusCls}`;
  card.innerHTML = `
    <div class="reco-head">
      <span class="reco-bias ${bias === "bullish" ? "up" : bias === "bearish" ? "down" : ""}">
        ${BIAS_LABEL[bias] || bias}</span>
      <span class="reco-status">${STATUS_LABEL[status] || status}</span>
    </div>
    ${rec.pattern ? `<div class="reco-pattern">${PATTERN_NAMES[rec.pattern] || rec.pattern} · H1</div>` : ""}
    ${levels}
    ${conf}
    ${rec.confidence_note ? `<div class="reco-conf-note">${rec.confidence_note}</div>` : ""}
    <ul class="reco-why">${(rec.rationale || []).map((r) => `<li>${r}</li>`).join("")}</ul>
    <div class="reco-when">${rec.created_at_wib || ""}</div>`;
  $("reco-sec").hidden = false;
}

function renderAccuracy(tracking) {
  const s = tracking.stats || {};
  const el = $("accuracy");
  if (!s.total) { el.hidden = true; return; }
  const rate = s.hit_rate != null ? (s.hit_rate * 100).toFixed(0) + "%" : "—";
  el.hidden = false;
  el.innerHTML = `
    <b>Feedback loop:</b> ${s.wins}/${s.resolved} rekomendasi mencapai TP1 (${rate})
    · ${s.losses} SL · ${s.timeouts} timeout · ${s.active} berjalan
    <span class="acc-when">— dinilai ${tracking.updated_at_wib || ""}</span>`;
}

async function loadRecommendation() {
  try {
    const res = await fetch("data/recommendation.json", { cache: "no-store" });
    if (res.ok) renderRecommendation(await res.json());
  } catch { /* recommendation.json belum ada — seksi tetap disembunyikan */ }
  try {
    const res = await fetch("data/tracking.json", { cache: "no-store" });
    if (res.ok) renderAccuracy(await res.json());
  } catch { /* tracking.json belum ada */ }
}

// ---- Fundamental: kalender bintang-3 US + berita kredibel ----

const fmtCal = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta", weekday: "short", day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

const H = 3_600_000; // satu jam dalam ms
// aturan jeda entry: identik backend (calendar.py): 2 jam sebelum - 1 jam sesudah rilis
const BLACKOUT_BEFORE = 2 * H;
const BLACKOUT_AFTER = 1 * H;

function countdown(ms) {
  const abs = Math.abs(ms);
  const h = Math.floor(abs / H), m = Math.round((abs % H) / 60_000);
  const unit = h > 0 ? `${h}j ${m}m` : `${m}m`;
  return ms >= 0 ? `dalam ${unit}` : `${unit} lalu`;
}

function renderCalendar(cal) {
  const now = Date.now();
  const events = cal.events || [];
  $("cal-source").textContent = cal.source === "tradingeconomics"
    ? "· TradingEconomics" : "· ForexFactory";

  // banner jeda entry (aturan identik backend)
  const blk = events.find((e) => {
    const t = new Date(e.t_utc).getTime();
    return t - BLACKOUT_BEFORE <= now && now <= t + BLACKOUT_AFTER;
  });
  const banner = $("blackout");
  if (blk) {
    const t = new Date(blk.t_utc).getTime();
    $("blackout-event").textContent = blk.title;
    $("blackout-note").textContent =
      `rilis ${fmtCal.format(new Date(blk.t_utc))} WIB (${countdown(t - now)}) — ` +
      `jangan buka posisi baru`;
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }

  const next = events
    .filter((e) => new Date(e.t_utc).getTime() > now)
    .sort((a, b) => a.t_utc.localeCompare(b.t_utc))
    .slice(0, 4);
  const el = $("calendar-list");
  if (!next.length) {
    el.textContent = "tidak ada event bintang-3 US dalam minggu ini";
  } else {
    el.innerHTML = next.map((e) => {
      const t = new Date(e.t_utc).getTime();
      const vals = [e.forecast ? `fcst ${e.forecast}` : null,
                    e.previous ? `prev ${e.previous}` : null,
                    e.actual ? `act ${e.actual}` : null].filter(Boolean).join(" · ");
      return `
        <div class="cal-row">
          <div>
            <div class="cal-title">${e.title}</div>
            <div class="cal-when">${fmtCal.format(new Date(e.t_utc))} WIB</div>
            ${vals ? `<div class="cal-vals">${vals}</div>` : ""}
          </div>
          <div class="cal-countdown">${countdown(t - now)}</div>
        </div>`;
    }).join("");
  }
  $("fundamental-sec").hidden = false;
}

function renderNews(news) {
  const el = $("news-list");
  const items = (news.items || []).slice(0, 6);
  if (!items.length) {
    el.textContent = "belum ada berita kredibel terkini";
  } else {
    el.innerHTML = items.map((i) => {
      const when = i.published_utc ? fmtCal.format(new Date(i.published_utc)) + " WIB" : "";
      return `
        <div class="news-row">
          <a href="${i.url}" target="_blank" rel="noopener">${i.title}</a>
          <div class="news-meta">${i.source}${when ? " · " + when : ""}</div>
        </div>`;
    }).join("");
  }
  $("fundamental-sec").hidden = false;
}

async function loadFundamental() {
  try {
    const res = await fetch("data/calendar.json", { cache: "no-store" });
    if (res.ok) renderCalendar(await res.json());
  } catch { /* kalender belum ada */ }
  try {
    const res = await fetch("data/news.json", { cache: "no-store" });
    if (res.ok) renderNews(await res.json());
  } catch { /* berita belum ada */ }
}

async function main() {
  initChart();
  document.querySelectorAll(".tf-btn").forEach((b) =>
    b.addEventListener("click", () => switchTf(b.dataset.tf)));

  try {
    render(await loadCandles("1h"));
  } catch (err) {
    $("meta-line").textContent = `gagal memuat data: ${err.message} — jalankan backfill dulu`;
  }
  loadMeta();
  loadPatterns();
  loadRecommendation();
  loadFundamental();
  pollSpot();
  setInterval(pollSpot, 30_000); // XAUS.com cache 30s — jangan lebih sering
  setInterval(loadMeta, 10 * 60_000);
  setInterval(loadPatterns, 10 * 60_000);
  setInterval(loadRecommendation, 10 * 60_000);
  setInterval(loadFundamental, 10 * 60_000);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

main();