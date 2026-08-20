"use strict";

const WATCHLIST_ROW_DELAY_MS = 700; // spaced out to avoid tripping Yahoo's burst rate limit
const DONUT_COLORS = ["#D9A44E", "#34C98A", "#7C8FE8", "#E876A0", "#4FC3C9", "#E1B44A", "#8A9099"];

let selectedSymbol = null;
const lastQuoteValues = {}; // symbol -> last seen price, for flash-on-change

function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return sign + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPlain(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
}
async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function loadingHtml(text) {
  return `<span class="status-line" style="margin:0"><span class="spinner"></span>${escapeHtml(text)}</span>`;
}
function reducedMotion() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ---- animated count-up for headline numbers --------------------------------

function animateCountUp(el, endValue, { decimals = 2, prefix = "", suffix = "", duration = 900 } = {}) {
  if (reducedMotion()) {
    el.textContent = prefix + endValue.toFixed(decimals) + suffix;
    return;
  }
  const start = 0;
  const startTime = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - startTime) / duration);
    const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
    const value = start + (endValue - start) * eased;
    el.textContent = prefix + value.toFixed(decimals) + suffix;
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// ---- instrument icons (small inline SVG glyphs) -----------------------------

const ICON_PATHS = {
  bar: '<path d="M5 16 L8 8 H16 L19 16 Z"/><path d="M5 16 H19"/>',
  drop: '<path d="M12 3C9 8 6 12 6 15a6 6 0 0 0 12 0c0-3-3-7-6-12z"/>',
  network: '<circle cx="7" cy="7" r="1.6"/><circle cx="17" cy="7" r="1.6"/><circle cx="12" cy="17" r="1.6"/><path d="M7 7l5 10M17 7l-5 10M7 7h10"/>',
  bars: '<path d="M5 19V11M12 19V5M19 19v-7"/>',
  dollar: '<path d="M12 4v16M16 7.5c0-1.5-1.8-2.5-4-2.5s-4 1-4 2.8c0 3.5 8 1.7 8 5.2 0 1.8-2 2.8-4 2.8s-4-1-4-2.5"/>',
};
const ICON_BY_SYMBOL = {
  GOLD: "bar", SILVER: "bar", OIL: "drop", BTC: "network", ETH: "network",
  SPX: "bars", NASDAQ: "bars", DXY: "dollar", EURUSD: "dollar",
};
function iconFor(name) {
  const key = ICON_BY_SYMBOL[name.toUpperCase()] || "bars";
  return `<span class="sym-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[key]}</svg></span>`;
}

// ---- tabs -------------------------------------------------------------

function positionTabUnderline(btn) {
  const underline = document.getElementById("tab-underline");
  if (!btn || !underline) return;
  underline.style.left = btn.offsetLeft + "px";
  underline.style.width = btn.offsetWidth + "px";
}
document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + btn.dataset.tab));
  positionTabUnderline(btn);
  // Lightweight Charts sized itself against a hidden (display:none, 0x0) panel if
  // that tab was never visited yet — ResizeObserver catches most of this, but a
  // belt-and-suspenders resize+refit right when the tab becomes visible avoids any
  // race between "panel just unhid" and "observer callback fires."
  requestAnimationFrame(() => {
    if (btn.dataset.tab === "chart") resizePriceChart();
    if (btn.dataset.tab === "backtest") resizeBacktestChart();
  });
});
window.addEventListener("resize", () => positionTabUnderline(document.querySelector("#tabs button.active")));

// ---- watchlist collapse (persisted) ------------------------------------------

function setWatchlistCollapsed(collapsed) {
  document.getElementById("watchlist-pane").classList.toggle("collapsed", collapsed);
  document.getElementById("watchlist-toggle").classList.toggle("collapsed", collapsed);
  try { localStorage.setItem("aurum-watchlist-collapsed", collapsed ? "1" : "0"); } catch (e) {}
}
document.getElementById("watchlist-toggle").addEventListener("click", () => {
  const collapsed = !document.getElementById("watchlist-pane").classList.contains("collapsed");
  setWatchlistCollapsed(collapsed);
});
(function restoreWatchlistCollapsed() {
  let saved = null;
  try { saved = localStorage.getItem("aurum-watchlist-collapsed"); } catch (e) {}
  if (saved === "1") setWatchlistCollapsed(true);
})();

// ---- account state ------------------------------------------------------

async function loadState() {
  const state = await getJSON("/api/state");
  document.getElementById("f-equity").value = state.equity;
  document.getElementById("f-peak").value = state.peak_equity;
  document.getElementById("f-pnl").value = state.realized_pnl_today;
}
document.getElementById("account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await getJSON("/api/state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      equity: parseFloat(document.getElementById("f-equity").value),
      peak_equity: parseFloat(document.getElementById("f-peak").value),
      realized_pnl_today: parseFloat(document.getElementById("f-pnl").value),
    }),
  });
});

// ---- watchlist (auto-refreshing: quotes re-poll on their own, no manual
//      click required — see startLiveWatchlistLoop below) --------------------

const WATCHLIST_AUTO_REFRESH_INTERVAL_MS = 45000; // matches the server's quote cache TTL, so this never asks Yahoo for anything fresher than it's already willing to serve
let watchlistDefs = []; // {name, ticker}[] — fetched once, quotes re-poll against this
let watchlistLiveAt = null;
let watchlistLastErrorCount = 0;

/** Resolves immediately if the tab is visible, otherwise waits for it to
 * become visible again — used to pause live polling while the tab is in the
 * background instead of burning requests nobody's looking at. */
function waitForVisible() {
  if (document.visibilityState === "visible") return Promise.resolve();
  return new Promise((resolve) => {
    function onChange() {
      if (document.visibilityState === "visible") {
        document.removeEventListener("visibilitychange", onChange);
        resolve();
      }
    }
    document.addEventListener("visibilitychange", onChange);
  });
}

function renderWatchlistStatus() {
  const status = document.getElementById("watchlist-status");
  if (!status) return;
  if (watchlistLastErrorCount > 0) {
    status.innerHTML = `<span class="live-dot error"></span>${watchlistLastErrorCount} error(s) — Yahoo may be rate-limiting; retrying automatically.`;
    status.classList.add("error");
    return;
  }
  if (!watchlistLiveAt) return;
  const secs = Math.max(0, Math.round((Date.now() - watchlistLiveAt) / 1000));
  status.innerHTML = `<span class="live-dot"></span>Live · updated ${secs}s ago`;
  status.classList.remove("error");
}

async function buildWatchlistRows() {
  const body = document.getElementById("watchlist-body");
  watchlistDefs = await getJSON("/api/watchlist");
  body.innerHTML = watchlistDefs
    .map(
      (w, i) =>
        `<tr class="watch-row" data-name="${w.name}" style="animation-delay:${i * 45}ms"><td><div class="sym-cell">${iconFor(w.name)}<span>${w.name}</span></div></td><td>${w.ticker}</td>` +
        `<td class="c-num" data-cell="last">—</td><td class="c-num" data-cell="high">—</td><td class="c-num" data-cell="low">—</td></tr>`
    )
    .join("");
  body.querySelectorAll("tr").forEach((row) => row.addEventListener("click", () => selectSymbol(row.dataset.name)));
  populateAnalyzeSymbolSelect();
}

function populateAnalyzeSymbolSelect() {
  const select = document.getElementById("analyze-symbol");
  if (!select) return;
  const current = select.value;
  select.innerHTML = watchlistDefs.map((w) => `<option value="${w.name}">${w.name}</option>`).join("");
  if (current && watchlistDefs.some((w) => w.name === current)) select.value = current;
}

async function refreshWatchlistQuotes() {
  const status = document.getElementById("watchlist-status");
  const body = document.getElementById("watchlist-body");
  let errors = 0;
  for (let i = 0; i < watchlistDefs.length; i++) {
    if (i > 0) await sleep(WATCHLIST_ROW_DELAY_MS);
    const { name } = watchlistDefs[i];
    status.innerHTML = `<span class="spinner"></span>Fetching quotes… (${i + 1}/${watchlistDefs.length})`;
    status.classList.remove("error");
    try {
      const quote = await getJSON(`/api/quote/${name}`);
      const row = body.querySelector(`tr[data-name="${name}"]`);
      const lastCell = row.querySelector('[data-cell="last"]');
      const prev = lastQuoteValues[name];
      lastCell.textContent = fmtPlain(quote.price);
      row.querySelector('[data-cell="high"]').textContent = fmtPlain(quote.day_high);
      row.querySelector('[data-cell="low"]').textContent = fmtPlain(quote.day_low);
      if (prev !== undefined && prev !== quote.price) {
        lastCell.classList.remove("flash-up", "flash-down");
        void lastCell.offsetWidth; // restart animation
        lastCell.classList.add(quote.price > prev ? "flash-up" : "flash-down");
      }
      lastQuoteValues[name] = quote.price;
    } catch (err) {
      errors++;
    }
  }
  watchlistLastErrorCount = errors;
  watchlistLiveAt = Date.now();
  renderWatchlistStatus();
  return errors;
}

async function loadWatchlist() {
  await buildWatchlistRows();
  await refreshWatchlistQuotes();
  if (!selectedSymbol && watchlistDefs.length) selectSymbol(watchlistDefs[0].name);
}
document.getElementById("refresh-quotes").addEventListener("click", () => refreshWatchlistQuotes());

async function startLiveWatchlistLoop() {
  while (true) {
    await sleep(WATCHLIST_AUTO_REFRESH_INTERVAL_MS);
    await waitForVisible();
    await refreshWatchlistQuotes();
  }
}

function selectSymbol(name) {
  selectedSymbol = name;
  document.querySelectorAll("#watchlist-body tr").forEach((r) => r.classList.toggle("selected", r.dataset.name === name));
  document.getElementById("chart-title").innerHTML = `${iconFor(name)} Chart — ${name}`;
  loadChart(name);
  const analyzeSelect = document.getElementById("analyze-symbol");
  if (analyzeSelect) analyzeSelect.value = name;
}

// ---- chart (TradingView Lightweight Charts: candles, volume, EMA overlay,
//      crosshair readout, real zoom/pan) -------------------------------------

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** EMA(period), seeded the same way the scanner's Python EMA is (first value
 * seeds the series) — so the line on the chart is the exact same number the
 * Setup Scanner's "Trend" confirmation is reading. */
function computeEMA(closes, period) {
  const k = 2 / (period + 1);
  let ema = null;
  return closes.map((c) => {
    ema = ema === null ? c : (c - ema) * k + ema;
    return ema;
  });
}

let priceChart = null, candleSeries = null, priceLineSeries = null, volumeSeries = null, emaSeries = null;

function ensurePriceChart() {
  if (priceChart) return;
  const container = document.getElementById("chart-container");
  priceChart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: { background: { color: "transparent" }, textColor: cssVar("--text-secondary"), fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
    grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: cssVar("--border") },
    timeScale: { borderColor: cssVar("--border"), timeVisible: true, secondsVisible: false },
  });
  candleSeries = priceChart.addCandlestickSeries({
    upColor: cssVar("--positive"), downColor: cssVar("--negative"), borderVisible: false,
    wickUpColor: cssVar("--positive"), wickDownColor: cssVar("--negative"),
  });
  priceLineSeries = priceChart.addLineSeries({ color: cssVar("--accent"), lineWidth: 2, visible: false });
  emaSeries = priceChart.addLineSeries({
    color: cssVar("--accent-strong"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  });
  volumeSeries = priceChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  const legend = document.getElementById("chart-legend");
  priceChart.subscribeCrosshairMove((param) => {
    const candle = param.time ? param.seriesData.get(candleSeries) : null;
    if (!candle) { legend.hidden = true; return; }
    const vol = param.seriesData.get(volumeSeries);
    const ema = param.seriesData.get(emaSeries);
    const date = new Date(param.time * 1000);
    const upDown = candle.close >= candle.open ? "pos" : "neg";
    legend.innerHTML =
      `<div>${date.toLocaleString()}</div>` +
      `<div>O <b>${fmtPlain(candle.open)}</b>  H <b>${fmtPlain(candle.high)}</b>  L <b>${fmtPlain(candle.low)}</b>  C <b class="${upDown}">${fmtPlain(candle.close)}</b></div>` +
      (vol ? `<div>Vol <b>${fmtPlain(vol.value)}</b></div>` : "") +
      (ema ? `<div>50-EMA <b class="accent-ink">${fmtPlain(ema.value)}</b></div>` : "");
    legend.hidden = false;
  });

  new ResizeObserver(resizePriceChart).observe(container);
}

function resizePriceChart() {
  if (!priceChart) return;
  const c = document.getElementById("chart-container");
  if (!c.clientWidth || !c.clientHeight) return;
  priceChart.applyOptions({ width: c.clientWidth, height: c.clientHeight });
}

// ---- chart state + loading ---------------------------------------------------

const TIMEFRAMES = {
  "1m": { range: "7d", interval: "1m" },
  "15m": { range: "60d", interval: "15m" },
  "1d": { range: "10y", interval: "1d" },
};
const CHART_DISPLAY_BARS = 150;
const EMA_PERIOD = 50;
// Read the starting state from whichever button HTML marks active, rather than
// hardcoding it separately — one source of truth, so the two can't desync.
let chartTimeframe = document.querySelector("#chart-timeframe button.active")?.dataset.tf || "1d";
let chartType = document.querySelector("#chart-type button.active")?.dataset.type || "line";
let currentChartBars = [];
let currentChartName = null;

document.getElementById("chart-timeframe").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tf]");
  if (!btn) return;
  document.querySelectorAll("#chart-timeframe button").forEach((b) => b.classList.toggle("active", b === btn));
  chartTimeframe = btn.dataset.tf;
  if (currentChartName) loadChart(currentChartName);
});
document.getElementById("chart-type").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-type]");
  if (!btn) return;
  document.querySelectorAll("#chart-type button").forEach((b) => b.classList.toggle("active", b === btn));
  chartType = btn.dataset.type;
  renderCurrentChart();
});

function renderCurrentChart() {
  if (!currentChartBars.length) return;
  ensurePriceChart();
  const recent = currentChartBars.slice(-CHART_DISPLAY_BARS);

  candleSeries.setData(recent.map((b) => ({ time: b.timestamp, open: b.open, high: b.high, low: b.low, close: b.close })));
  priceLineSeries.setData(recent.map((b) => ({ time: b.timestamp, value: b.close })));
  volumeSeries.setData(
    recent.map((b) => ({ time: b.timestamp, value: b.volume, color: b.close >= b.open ? cssVar("--positive") : cssVar("--negative") }))
  );
  const emaValues = computeEMA(recent.map((b) => b.close), EMA_PERIOD);
  emaSeries.setData(recent.map((b, i) => ({ time: b.timestamp, value: emaValues[i] })));

  candleSeries.applyOptions({ visible: chartType === "candles" });
  priceLineSeries.applyOptions({ visible: chartType === "line" });

  priceChart.timeScale().fitContent();
}

const CHART_LIVE_POLL_INTERVAL_MS = 45000; // matches the server's quote cache TTL, same reasoning as the watchlist loop
let lastChartLiveAt = null;
let chartSummaryBaseText = "";

function renderChartSummary() {
  const summary = document.getElementById("chart-summary");
  if (!chartSummaryBaseText) return;
  let liveSuffix = "";
  if (lastChartLiveAt) {
    const secs = Math.max(0, Math.round((Date.now() - lastChartLiveAt) / 1000));
    liveSuffix = ` · <span class="live-dot"></span>live, updated ${secs}s ago`;
  }
  summary.innerHTML = chartSummaryBaseText + liveSuffix;
}

async function loadChart(name) {
  currentChartName = name;
  lastChartLiveAt = null;
  chartSummaryBaseText = "";
  const summary = document.getElementById("chart-summary");
  summary.innerHTML = loadingHtml("Loading…");
  summary.classList.remove("error");
  try {
    const { range, interval } = TIMEFRAMES[chartTimeframe];
    const bars = await getJSON(`/api/history/${name}?range=${range}&interval=${interval}`);
    currentChartBars = bars;
    renderCurrentChart();
    const recent = bars.slice(-CHART_DISPLAY_BARS);
    const change = recent.length > 1 ? ((recent[recent.length - 1].close - recent[0].close) / recent[0].close) * 100 : 0;
    chartSummaryBaseText = `${bars.length} ${chartTimeframe} bars cached · last ${recent.length} shown · ${fmtPct(change)} over that window · scroll to zoom, drag to pan`;
    renderChartSummary();
  } catch (err) {
    currentChartBars = [];
    summary.textContent = `Could not load history: ${err.message}`;
    summary.classList.add("error");
  }
}

/** Pulls the latest quote for the symbol currently on screen and pushes it
 * into the existing last bar via series.update() — a single cheap request
 * that keeps the chart ticking without refetching the whole history. */
async function updateLiveChartPoint(name) {
  if (!priceChart || !currentChartBars.length) return;
  let quote;
  try {
    quote = await getJSON(`/api/quote/${name}`);
  } catch (err) {
    return; // a single missed live tick isn't worth surfacing as an error
  }
  if (name !== currentChartName || !currentChartBars.length) return;
  const bars = currentChartBars;
  const last = bars[bars.length - 1];
  const updated = { ...last, close: quote.price, high: Math.max(last.high, quote.price), low: Math.min(last.low, quote.price) };
  bars[bars.length - 1] = updated;
  candleSeries.update({ time: updated.timestamp, open: updated.open, high: updated.high, low: updated.low, close: updated.close });
  priceLineSeries.update({ time: updated.timestamp, value: updated.close });
  volumeSeries.update({ time: updated.timestamp, value: updated.volume, color: updated.close >= updated.open ? cssVar("--positive") : cssVar("--negative") });
  const recent = bars.slice(-CHART_DISPLAY_BARS);
  const emaValues = computeEMA(recent.map((b) => b.close), EMA_PERIOD);
  emaSeries.update({ time: updated.timestamp, value: emaValues[emaValues.length - 1] });
  lastChartLiveAt = Date.now();
  renderChartSummary();
}

async function startLiveChartLoop() {
  while (true) {
    await sleep(CHART_LIVE_POLL_INTERVAL_MS);
    await waitForVisible();
    if (currentChartName) await updateLiveChartPoint(currentChartName);
  }
}

// ---- gauges (radial arcs, used by the Risk panel) ---------------------------

function renderGauge(container, { label, valuePct, limitPct, size = 108 }) {
  const r = 40, stroke = 9, c = 2 * Math.PI * r;
  const ratio = limitPct > 0 ? Math.min(1.15, valuePct / limitPct) : 0; // allow slight over-limit visual
  const dash = Math.min(1, ratio) * c;
  const over = valuePct > limitPct;
  const color = over ? "var(--negative)" : ratio > 0.75 ? "var(--accent)" : "var(--positive)";
  const id = "g" + Math.random().toString(36).slice(2, 8);
  const html = `
    <div class="gauge">
      <svg width="${size}" height="${size}" viewBox="0 0 100 100">
        <circle class="gauge-ring-bg" cx="50" cy="50" r="${r}"/>
        <circle id="${id}" class="gauge-ring-fill" cx="50" cy="50" r="${r}" stroke="${color}"
          stroke-dasharray="${c}" stroke-dashoffset="${c}"/>
        <text x="50" y="54" text-anchor="middle" class="gauge-value" fill="var(--text-primary)" font-size="15">${valuePct.toFixed(1)}%</text>
      </svg>
      <span class="gauge-label">${escapeHtml(label)}<br/>limit ${limitPct.toFixed(0)}%</span>
    </div>`;
  container.insertAdjacentHTML("beforeend", html);
  requestAnimationFrame(() => {
    const ring = document.getElementById(id);
    if (ring) ring.style.strokeDashoffset = String(c - dash);
  });
}

// ---- donut (allocation weights, used by Fund + Optimizer panels) ------------

function renderDonut(container, weights) {
  const entries = Object.entries(weights).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { container.hidden = true; return; }
  container.hidden = false;
  const r = 46, stroke = 16, c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = entries
    .map(([name, w], i) => {
      const color = DONUT_COLORS[i % DONUT_COLORS.length];
      const len = w * c;
      const dashoffset = c - offset;
      offset += len;
      return `<circle class="donut-seg" cx="60" cy="60" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
        stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${dashoffset}" transform="rotate(-90 60 60)"
        style="transition-delay:${i * 90}ms" />`;
    })
    .join("");
  const legend = entries
    .map(([name, w], i) => `<div class="donut-legend-item"><span class="donut-swatch" style="background:${DONUT_COLORS[i % DONUT_COLORS.length]}"></span>${name} <span class="muted">${(w * 100).toFixed(1)}%</span></div>`)
    .join("");
  container.innerHTML = `<svg class="donut-ring" width="120" height="120" viewBox="0 0 120 120">${arcs}</svg><div class="donut-legend">${legend}</div>`;
}

// ---- setup scanner --------------------------------------------------------

document.getElementById("run-scanner").addEventListener("click", async () => {
  const out = document.getElementById("scanner-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Scanning…");
  try {
    const r = await getJSON(`/api/scan/${selectedSymbol}`);
    const lines = [
      `Pattern: ${r.pattern}    Score: ${r.score_pct.toFixed(0)}%    Setup detected: ${r.setup_detected ? "YES" : "no"}`,
      `Last close ${fmtPlain(r.last_close)}  ·  50-EMA ${fmtPlain(r.ema)}`,
      "",
      ...r.confirmations.map((c) => `${c.confirmed ? "✓" : "✗"} ${c.name.padEnd(11)} ${c.detail}`),
    ];
    out.innerHTML = lines.map((l) => escapeHtml(l)).join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- analyze: one symbol in, one detailed invest/avoid report out -----------

function renderHorizonCard(title, plan) {
  return `
    <div class="horizon-card">
      <h4>${escapeHtml(title)} <span class="direction-tag ${plan.direction}">${plan.direction.toUpperCase()}</span></h4>
      <div class="holding-period">${escapeHtml(plan.holding_period)}</div>
      <div class="plan-grid">
        <div><div class="plan-label">Entry</div><div class="plan-value">${fmtPlain(plan.entry)}</div></div>
        <div><div class="plan-label">Stop</div><div class="plan-value">${fmtPlain(plan.stop)}</div></div>
        <div><div class="plan-label">Take profit 1</div><div class="plan-value">${fmtPlain(plan.take_profit_1)}</div></div>
        <div><div class="plan-label">Take profit 2</div><div class="plan-value">${fmtPlain(plan.take_profit_2)}</div></div>
        <div><div class="plan-label">Risk / unit</div><div class="plan-value">${fmtPlain(plan.risk_per_unit)}</div></div>
        <div><div class="plan-label">R:R to TP1</div><div class="plan-value">${plan.risk_reward_ratio.toFixed(2)}</div></div>
      </div>
      ${plan.notes.length ? `<div class="plan-notes">${plan.notes.map((n) => escapeHtml(n)).join("<br>")}</div>` : ""}
    </div>`;
}

function macroDeltaArrow(series) {
  if (series.previous_value === null || series.previous_value === undefined) return "";
  if (series.latest_value > series.previous_value) return ` <span class="pos">▲</span>`;
  if (series.latest_value < series.previous_value) return ` <span class="neg">▼</span>`;
  return "";
}

function renderMacroSection(macro) {
  if (!macro || !macro.length) return "";
  return `
    <h4 class="report-section-title">Macro backdrop <span class="muted" style="font-weight:400">— via FRED, not symbol-specific</span></h4>
    <div class="research-grid">
      ${macro
        .map(
          (s) =>
            `<div class="research-stat"><div class="stat-label">${escapeHtml(s.label)}</div>` +
            `<div class="stat-value">${s.latest_value.toFixed(2)}${macroDeltaArrow(s)}</div>` +
            `<div class="muted" style="font-size:0.64rem;margin-top:3px">as of ${escapeHtml(s.latest_date)}</div></div>`
        )
        .join("")}
    </div>`;
}

function renderNewsSection(news, earnings) {
  if ((!news || !news.length) && !earnings) return "";
  const earningsHtml = earnings
    ? `<div class="news-earnings"><strong>Next earnings:</strong> ${escapeHtml(earnings.date)}` +
      (earnings.eps_estimate !== null && earnings.eps_estimate !== undefined ? ` · EPS estimate ${earnings.eps_estimate}` : "") +
      `</div>`
    : "";
  const newsHtml =
    news && news.length
      ? `<ul class="news-list">${news
          .map((n) => `<li><a href="${escapeHtml(n.url)}" target="_blank" rel="noopener">${escapeHtml(n.headline)}</a> <span class="muted">— ${escapeHtml(n.source)}</span></li>`)
          .join("")}</ul>`
      : "";
  return `
    <h4 class="report-section-title">News &amp; earnings <span class="muted" style="font-weight:400">— via Finnhub, stock tickers only</span></h4>
    ${earningsHtml}${newsHtml}`;
}

function renderAnalysis(r) {
  const out = document.getElementById("analyze-output");
  const research = r.research;
  out.innerHTML = `
    <span class="verdict-pill verdict-${r.verdict}">${r.verdict}</span><span class="confidence-tag">${r.confidence} confidence</span>
    <p style="margin:10px 0 4px">${escapeHtml(r.summary)}</p>

    <div class="research-grid">
      <div class="research-stat"><div class="stat-label">Last close</div><div class="stat-value">${fmtPlain(r.last_close)}</div></div>
      <div class="research-stat"><div class="stat-label">200-day trend</div><div class="stat-value">${escapeHtml(research.trend_regime)}</div></div>
      <div class="research-stat"><div class="stat-label">50-EMA</div><div class="stat-value">${fmtPlain(research.short_term_ema)}</div></div>
      <div class="research-stat"><div class="stat-label">200-EMA</div><div class="stat-value">${fmtPlain(research.long_term_ema)}</div></div>
      <div class="research-stat"><div class="stat-label">Momentum (21d)</div><div class="stat-value ${research.momentum_pct >= 0 ? "pos" : "neg"}">${fmtPct(research.momentum_pct)}</div></div>
      <div class="research-stat"><div class="stat-label">Ann. volatility</div><div class="stat-value">${research.volatility_annualized_pct.toFixed(1)}%</div></div>
      <div class="research-stat"><div class="stat-label">1y high</div><div class="stat-value">${fmtPlain(research.year_high)} <span class="muted">(${fmtPct(research.distance_from_year_high_pct)})</span></div></div>
      <div class="research-stat"><div class="stat-label">1y low</div><div class="stat-value">${fmtPlain(research.year_low)} <span class="muted">(${fmtPct(research.distance_from_year_low_pct)})</span></div></div>
    </div>

    ${renderMacroSection(r.macro)}
    ${renderNewsSection(r.news, r.earnings)}

    <div class="debate-columns">
      <div class="debate-col bull"><h4>Bull case</h4><ul>${r.debate.bull_points.map((p) => `<li>${escapeHtml(p)}</li>`).join("") || "<li>No bullish signals confirmed.</li>"}</ul></div>
      <div class="debate-col bear"><h4>Bear case</h4><ul>${r.debate.bear_points.map((p) => `<li>${escapeHtml(p)}</li>`).join("") || "<li>No bearish signals confirmed.</li>"}</ul></div>
    </div>

    <div class="horizon-cards">
      ${renderHorizonCard("Day trade", r.day_trade)}
      ${renderHorizonCard("Long-term hold", r.long_term)}
    </div>

    <div style="margin-top:16px">
      <span class="verdict-pill verdict-${r.risk.passed ? "APPROVED" : "REJECTED"}">${r.risk.status}</span>
      ${r.risk.checks.map((c) => `<div class="check-row"><span class="${c.passed ? "check-pass" : "check-fail"}">${c.passed ? "✓" : "✗"}</span> <strong>${c.name}</strong> — ${escapeHtml(c.detail)}</div>`).join("")}
    </div>`;
}

document.getElementById("analyze-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const select = document.getElementById("analyze-symbol");
  const symbol = select.value;
  const out = document.getElementById("analyze-output");
  if (!symbol) { out.textContent = "Pick a symbol first."; return; }
  out.innerHTML = loadingHtml(`Analyzing ${escapeHtml(symbol)}…`);
  try {
    const r = await getJSON(`/api/analyze/${symbol}`);
    renderAnalysis(r);
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- decision memo + risk --------------------------------------------------

document.getElementById("run-decision").addEventListener("click", async () => {
  const out = document.getElementById("decision-output");
  const riskOut = document.getElementById("risk-output");
  const gauges = document.getElementById("risk-gauges");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Building memo…");
  try {
    const r = await getJSON(`/api/decision/${selectedSymbol}`);
    out.innerHTML =
      `<span class="verdict-pill verdict-${r.verdict}">${r.verdict}</span>\n\n` +
      `Plan: ${r.plan.direction.toUpperCase()}  entry ${fmtPlain(r.plan.entry)}  stop ${fmtPlain(r.plan.stop)}  target ${fmtPlain(r.plan.target)}  R:R ${r.plan.risk_reward_ratio.toFixed(2)}\n` +
      `Setup: ${r.scan.pattern} (${r.scan.score_pct.toFixed(0)}% confirmed)\n\n` +
      (r.reasons.length ? "Why:\n" + r.reasons.map((x) => "  • " + escapeHtml(x)).join("\n") : "All checks clear.");

    riskOut.innerHTML =
      `<span class="verdict-pill verdict-${r.risk.passed ? "APPROVED" : "REJECTED"}">${r.risk.status}</span>\n\n` +
      r.risk.checks
        .map((c) => `<div class="check-row"><span class="${c.passed ? "check-pass" : "check-fail"}">${c.passed ? "✓" : "✗"}</span> <strong>${c.name}</strong> — ${escapeHtml(c.detail)}</div>`)
        .join("");

    gauges.innerHTML = "";
    renderGauge(gauges, { label: "Position size", valuePct: r.risk.position_size_pct, limitPct: 1.0 });
    renderGauge(gauges, { label: "Exposure", valuePct: r.risk.exposure_pct, limitPct: 50.0 });
    renderGauge(gauges, { label: "Drawdown", valuePct: r.risk.drawdown_pct, limitPct: 20.0 });
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- optimizer -------------------------------------------------------------

document.getElementById("run-optimizer").addEventListener("click", async () => {
  const out = document.getElementById("optimizer-output");
  const donut = document.getElementById("optimizer-donut");
  const method = document.getElementById("optimizer-method").value;
  out.innerHTML = loadingHtml("Loading watchlist history and optimizing… (first run can take a few seconds)");
  donut.hidden = true;
  try {
    const r = await getJSON(`/api/optimize?method=${method}`);
    renderDonut(donut, r.weights);
    const rows = Object.entries(r.weights).sort((a, b) => b[1] - a[1]);
    const lines = [
      `Method: ${r.method}`,
      "",
      ...rows.map(([name, w]) => `  ${name.padEnd(10)} ${(w * 100).toFixed(1)}%`),
      "",
      `Ann. return   ${fmtPct(r.expected_return_annual * 100)}`,
      `Ann. vol      ${(r.volatility_annual * 100).toFixed(2)}%`,
      `Sharpe        ${r.sharpe_ratio.toFixed(2)}`,
    ];
    if (r.skipped_symbols.length) lines.push("", `Skipped (no data): ${r.skipped_symbols.join(", ")}`);
    out.textContent = lines.join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- forecast ---------------------------------------------------------------

document.getElementById("run-forecast-baseline").addEventListener("click", async () => {
  const out = document.getElementById("forecast-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Forecasting…");
  try {
    const r = await getJSON(`/api/forecast/baseline/${selectedSymbol}?horizon=10`);
    const lines = [`Method: ${r.method}`, ""];
    r.point_forecast.forEach((p, i) => {
      lines.push(`  +${i + 1}d   ${fmtPlain(p).padStart(10)}   [${fmtPlain(r.lower_band[i])} .. ${fmtPlain(r.upper_band[i])}]`);
    });
    lines.push("", r.note);
    out.textContent = lines.join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

document.getElementById("run-forecast-kronos").addEventListener("click", async () => {
  const out = document.getElementById("forecast-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Loading Kronos-mini (first run downloads ~30MB of weights)…");
  try {
    const r = await getJSON(`/api/forecast/kronos/${selectedSymbol}?horizon=10`);
    const lines = [`Method: ${r.method}`, ""];
    r.point_forecast.forEach((p, i) => lines.push(`  +${i + 1}d   ${fmtPlain(p).padStart(10)}`));
    lines.push("", r.note);
    out.textContent = lines.join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- backtest chart (Lightweight Charts, same engine as the price chart) ----

let backtestChart = null, strategySeries = null, buyHoldSeries = null;

function ensureBacktestChart() {
  if (backtestChart) return;
  const container = document.getElementById("backtest-container");
  backtestChart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: { background: { color: "transparent" }, textColor: cssVar("--text-secondary"), fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
    grid: { vertLines: { color: cssVar("--border") }, horzLines: { color: cssVar("--border") } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: cssVar("--border") },
    timeScale: { borderColor: cssVar("--border"), timeVisible: true, secondsVisible: false },
  });
  strategySeries = backtestChart.addLineSeries({ color: cssVar("--accent"), lineWidth: 2 });
  buyHoldSeries = backtestChart.addLineSeries({ color: cssVar("--text-muted"), lineWidth: 2 });

  const legend = document.getElementById("backtest-legend");
  backtestChart.subscribeCrosshairMove((param) => {
    if (!param.time) { legend.hidden = true; return; }
    const s = param.seriesData.get(strategySeries);
    const b = param.seriesData.get(buyHoldSeries);
    if (!s && !b) { legend.hidden = true; return; }
    const date = new Date(param.time * 1000);
    legend.innerHTML =
      `<div>${date.toLocaleDateString()}</div>` +
      (s ? `<div><span class="accent-ink">Strategy</span> <b>${fmtMoney(s.value)}</b></div>` : "") +
      (b ? `<div>Buy &amp; hold <b>${fmtMoney(b.value)}</b></div>` : "");
    legend.hidden = false;
  });

  new ResizeObserver(resizeBacktestChart).observe(container);
}

function resizeBacktestChart() {
  if (!backtestChart) return;
  const c = document.getElementById("backtest-container");
  if (!c.clientWidth || !c.clientHeight) return;
  backtestChart.applyOptions({ width: c.clientWidth, height: c.clientHeight });
}

// ---- backtest -----------------------------------------------------------------

document.getElementById("run-backtest").addEventListener("click", async () => {
  const out = document.getElementById("backtest-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Running backtest on real history…");
  try {
    const r = await getJSON(`/api/backtest/${selectedSymbol}`);
    ensureBacktestChart();
    strategySeries.setData(r.strategy_equity_curve.map((p) => ({ time: p.timestamp, value: p.equity })));
    buyHoldSeries.setData(r.buy_hold_equity_curve.map((p) => ({ time: p.timestamp, value: p.equity })));
    backtestChart.timeScale().fitContent();
    const s = r.stats;
    out.innerHTML =
      `Data: ${r.bar_count} real daily bars\n\n` +
      `Starting equity   ${fmtMoney(s.starting_equity)}\n` +
      `Ending equity     <span id="bt-ending-equity"></span>\n` +
      `Total return      <span id="bt-total-return"></span>\n` +
      `Trades            ${s.total_trades}\n` +
      `Win rate          ${s.win_rate_pct.toFixed(1)}%\n` +
      `Avg win / loss    ${fmtMoney(s.avg_win)} / ${fmtMoney(s.avg_loss)}\n` +
      `Profit factor     ${s.profit_factor === Infinity ? "inf" : s.profit_factor.toFixed(2)}\n` +
      `Max drawdown      ${s.max_drawdown_pct.toFixed(2)}%`;
    const endEl = document.getElementById("bt-ending-equity");
    const retEl = document.getElementById("bt-total-return");
    endEl.className = s.ending_equity >= s.starting_equity ? "pos" : "neg";
    retEl.className = s.total_return_pct >= 0 ? "pos" : "neg";
    animateCountUp(endEl, s.ending_equity, { decimals: 2, prefix: "$" });
    animateCountUp(retEl, s.total_return_pct, { decimals: 2, prefix: s.total_return_pct >= 0 ? "+" : "", suffix: "%" });
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- fund (whole-watchlist scan + allocation) --------------------------------

document.getElementById("run-fund").addEventListener("click", async () => {
  const summary = document.getElementById("fund-summary");
  const table = document.getElementById("fund-table");
  const body = document.getElementById("fund-body");
  const donut = document.getElementById("fund-donut");
  summary.innerHTML = loadingHtml("Scanning the whole watchlist… (reuses cached history where possible, so this is usually quick)");
  table.hidden = true;
  donut.hidden = true;

  try {
    const r = await getJSON("/api/fund");
    const alloc = r.allocation ? r.allocation.weights : {};
    if (r.allocation) renderDonut(donut, alloc);

    body.innerHTML = r.entries
      .map((e, i) => {
        const rowStyle = `style="animation-delay:${i * 60}ms"`;
        if (e.error) {
          return `<tr class="watch-row" ${rowStyle}><td>${iconFor(e.symbol)} ${e.symbol}</td><td colspan="5" class="muted">${escapeHtml(e.error)}</td></tr>`;
        }
        const m = e.memo;
        const weight = alloc[e.symbol];
        const weightPct = weight !== undefined ? weight * 100 : 0;
        return (
          `<tr class="watch-row" ${rowStyle}><td><div class="sym-cell">${iconFor(e.symbol)}<span>${e.symbol}</span></div></td><td>${m.scan.pattern}</td>` +
          `<td class="c-num">${m.scan.score_pct.toFixed(0)}%</td>` +
          `<td><span class="verdict-pill verdict-${m.verdict}">${m.verdict}</span></td>` +
          `<td class="c-num">${m.plan.risk_reward_ratio.toFixed(2)}</td>` +
          `<td class="c-num">${weight !== undefined ? weightPct.toFixed(1) + "%" : "—"}` +
          (weight !== undefined ? `<div class="allocation-bar-track"><div class="allocation-bar-fill" data-w="${weightPct}"></div></div>` : "") +
          `</td></tr>`
        );
      })
      .join("");
    table.hidden = false;
    requestAnimationFrame(() => {
      body.querySelectorAll(".allocation-bar-fill").forEach((el) => { el.style.width = el.dataset.w + "%"; });
    });

    const approvedCount = r.approved_symbols.length;
    const watchlistCount = r.watchlist_symbols.length;
    const lines = [
      `${approvedCount} approved, ${watchlistCount} on watchlist, out of ${r.entries.length} scanned.`,
    ];
    if (r.allocation) {
      lines.push(
        "",
        `Suggested split (${r.allocation.method}) across ${Object.keys(alloc).length} name(s):`,
        `Ann. return ${fmtPct(r.allocation.expected_return_annual * 100)}  ·  Ann. vol ${(r.allocation.volatility_annual * 100).toFixed(2)}%  ·  Sharpe ${r.allocation.sharpe_ratio.toFixed(2)}`
      );
    } else {
      lines.push("", "No allocation yet — fewer than 2 symbols have enough approved/scannable history.");
    }
    summary.textContent = lines.join("\n");
  } catch (err) {
    summary.textContent = "Error: " + err.message;
  }
});

// ---- boot ---------------------------------------------------------------------

loadState();
loadWatchlist().then(startLiveWatchlistLoop);
startLiveChartLoop();
setInterval(() => { renderWatchlistStatus(); renderChartSummary(); }, 1000); // ticks the "updated Ns ago" text between real polls
requestAnimationFrame(() => positionTabUnderline(document.querySelector("#tabs button.active")));
