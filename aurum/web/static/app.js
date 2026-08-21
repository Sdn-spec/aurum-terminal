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

// ---- views: Watchlist (home) / Symbol detail (drill-down) / Fund / Optimizer --

/** Symbol detail isn't one of the top-nav destinations (it's reached only by
 * clicking a watchlist row, with its own back button), so switching to it
 * leaves every top-nav button inactive — intentional, there's no "you are
 * here" button for a page you can only arrive at via drill-down. */
function switchView(viewName) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + viewName));
  document.querySelectorAll("#topnav button[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === viewName));
  // Lightweight Charts sized itself against a hidden (display:none, 0x0) container
  // if the symbol view was never opened yet — ResizeObserver catches most of this,
  // but a belt-and-suspenders resize+refit right when it becomes visible avoids any
  // race between "view just unhid" and "observer callback fires."
  if (viewName === "symbol") {
    requestAnimationFrame(() => { resizePriceChart(); resizeBacktestChart(); });
  }
  if (viewName === "optimizer") loadCorrelation();
  if (viewName === "journal") loadJournal();
  if (viewName === "markets") loadMarkets();
}
document.getElementById("topnav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  switchView(btn.dataset.view);
});
document.getElementById("back-to-watchlist").addEventListener("click", () => switchView("watchlist"));

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

// Shorter than the server's 45s quote cache TTL on purpose: polling more often
// than the cache refreshes doesn't cost anything extra against Yahoo (most of
// these ticks just re-serve the same cached value), but it does mean a price
// that genuinely changed server-side shows up here within ~15s instead of
// waiting up to 45s for the next cycle -- the gap between "the data is fresh"
// and "the screen shows it," not real request volume, is what this shortens.
const WATCHLIST_AUTO_REFRESH_INTERVAL_MS = 15000;
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
  if (!watchlistLiveAt) return;
  // The live pulse and the "updated Ns ago" ticker must always show, even when
  // a symbol is failing -- some symbols (NASDAQ, SILVER, OIL) are known gaps on
  // the free data tier and fail on essentially every cycle, so gating the live
  // indicator on "zero errors" meant it was permanently hidden behind a static
  // error message instead of ticking alongside it. Found live: reported as
  // "the Live indicator never moves," traced to exactly this early return.
  const secs = Math.max(0, Math.round((Date.now() - watchlistLiveAt) / 1000));
  const liveText = `<span class="live-dot${watchlistLastErrorCount > 0 ? " error" : ""}"></span>Live · updated ${secs}s ago`;
  if (watchlistLastErrorCount > 0) {
    status.innerHTML = `${liveText} · ${watchlistLastErrorCount} symbol(s) failing — Yahoo may be rate-limiting; retrying automatically.`;
    status.classList.add("error");
  } else {
    status.innerHTML = liveText;
    status.classList.remove("error");
  }
}

async function buildWatchlistRows() {
  const body = document.getElementById("watchlist-body");
  watchlistDefs = await getJSON("/api/watchlist");
  body.innerHTML = watchlistDefs
    .map(
      (w, i) =>
        `<tr class="watch-row" data-name="${w.name}" style="animation-delay:${i * 45}ms"><td><div class="sym-cell">${iconFor(w.name)}<span class="sym-name">${w.name}</span></div></td><td>${w.ticker}</td>` +
        `<td class="c-num" data-cell="last">—</td><td class="c-num" data-cell="high">—</td><td class="c-num" data-cell="low">—</td>` +
        `<td class="watch-actions"><button type="button" class="row-edit-btn" title="Rename">✎</button><button type="button" class="row-delete-btn" title="Remove">✕</button></td></tr>`
    )
    .join("");
  populateAlertSymbolSelect();
}

function populateAlertSymbolSelect() {
  const select = document.getElementById("alert-symbol");
  if (!select) return;
  const current = select.value;
  select.innerHTML = watchlistDefs.map((w) => `<option value="${w.name}">${w.name}</option>`).join("");
  if (current && watchlistDefs.some((w) => w.name === current)) select.value = current;
}

// A single delegated listener (rather than one per row) survives buildWatchlistRows()
// rebuilding the whole tbody on every add/remove/rename — no re-attaching needed.
document.getElementById("watchlist-body").addEventListener("click", (e) => {
  const deleteBtn = e.target.closest(".row-delete-btn");
  if (deleteBtn) {
    e.stopPropagation();
    deleteWatchlistSymbol(deleteBtn.closest("tr").dataset.name);
    return;
  }
  const editBtn = e.target.closest(".row-edit-btn");
  if (editBtn) {
    e.stopPropagation();
    startInlineEdit(editBtn.closest("tr"));
    return;
  }
  if (e.target.closest(".row-edit-input")) return; // don't navigate away while mid-edit
  const row = e.target.closest("tr[data-name]");
  if (row) openSymbol(row.dataset.name);
});

async function deleteWatchlistSymbol(name) {
  const status = document.getElementById("watchlist-status");
  try {
    await getJSON(`/api/watchlist/${name}`, { method: "DELETE" });
    delete lastQuoteValues[name];
    await loadWatchlist();
  } catch (err) {
    status.textContent = `Could not remove ${name}: ${err.message}`;
    status.classList.add("error");
  }
}

function startInlineEdit(row) {
  const name = row.dataset.name;
  const nameSpan = row.querySelector(".sym-name");
  const original = nameSpan.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "row-edit-input";
  input.value = original;
  nameSpan.replaceWith(input);
  input.focus();
  input.select();

  let canceled = false;
  function revert() {
    canceled = true;
    if (input.isConnected) input.replaceWith(nameSpan);
  }

  async function commit() {
    if (canceled) return;
    const newName = input.value.trim().toUpperCase();
    if (!newName || newName === original) { revert(); return; }
    input.disabled = true;
    const status = document.getElementById("watchlist-status");
    try {
      await getJSON(`/api/watchlist/${name}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newName }),
      });
      if (lastQuoteValues[name] !== undefined) {
        lastQuoteValues[newName] = lastQuoteValues[name];
        delete lastQuoteValues[name];
      }
      await loadWatchlist(); // rebuilds the whole table, including this row under its new name
    } catch (err) {
      input.disabled = false;
      status.textContent = `Could not rename ${name}: ${err.message}`;
      status.classList.add("error");
    }
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    if (e.key === "Escape") { e.preventDefault(); revert(); }
  });
  input.addEventListener("blur", commit);
}

document.getElementById("watchlist-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("watchlist-add-input");
  const name = input.value.trim().toUpperCase();
  if (!name) return;
  const status = document.getElementById("watchlist-status");
  try {
    await getJSON("/api/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
    });
    input.value = "";
    await loadWatchlist();
  } catch (err) {
    status.textContent = `Could not add ${name}: ${err.message}`;
    status.classList.add("error");
  }
});

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
      // Bumped here, per symbol, rather than only once after the whole loop --
      // a single stuck symbol can spend a long time inside Yahoo's own retry
      // backoff (worst case: several failed attempts, each with its own
      // timeout), and gating the "Live" timestamp on the entire 7-symbol batch
      // finishing meant one slow straggler froze the indicator for everyone
      // else too, even while their prices kept updating underneath it fine.
      // Caught by watching the real site patiently: prices were genuinely
      // still moving while "updated Ns ago" sat frozen for over a minute.
      watchlistLiveAt = Date.now();
      // the symbol detail header (price, day change, stats row) rides this same
      // poll cycle, so opening a stock doesn't need a second/duplicate quote fetch
      if (selectedSymbol === name) {
        currentSymbolQuote = quote;
        renderSymbolHeaderPrice(quote);
        renderSymbolStats();
      }
      await checkPriceAlerts(name, quote.price);
    } catch (err) {
      errors++;
    }
  }
  // if literally every symbol failed this cycle, watchlistLiveAt never got
  // touched above -- fall back to "now" so the status line still shows
  // something (an error, honestly timestamped) instead of staying blank,
  // which is what renderWatchlistStatus() does when watchlistLiveAt is null
  if (!watchlistLiveAt) watchlistLiveAt = Date.now();
  watchlistLastErrorCount = errors;
  renderWatchlistStatus();
  return errors;
}

async function loadWatchlist() {
  await buildWatchlistRows();
  await refreshWatchlistQuotes();
}
document.getElementById("refresh-quotes").addEventListener("click", () => refreshWatchlistQuotes());

// ---- price alerts (one-shot, checked client-side against the live poll) -----
// No background poller exists in this app -- these rules are just persisted
// server-side. The actual check happens here, every ~45s, against the same
// quotes refreshWatchlistQuotes() already fetches for the table.

let priceAlerts = []; // {id, symbol, condition, price}[]

async function loadAlerts() {
  priceAlerts = await getJSON("/api/alerts");
  renderAlertsList();
}

function renderAlertsList() {
  const out = document.getElementById("alerts-list");
  if (!out) return;
  if (!priceAlerts.length) { out.textContent = "No alerts set."; return; }
  out.innerHTML = priceAlerts
    .map(
      (a) =>
        `<div class="alert-row" data-id="${a.id}"><span>${escapeHtml(a.symbol)} ${a.condition === "above" ? "▲ above" : "▼ below"} ${fmtPlain(a.price)}</span>` +
        `<button type="button" class="alert-delete-btn" data-id="${a.id}" title="Remove">✕</button></div>`
    )
    .join("");
}

document.getElementById("alerts-list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".alert-delete-btn");
  if (!btn) return;
  try {
    await getJSON(`/api/alerts/${btn.dataset.id}`, { method: "DELETE" });
  } catch (err) {
    // already gone (e.g. it just fired elsewhere) -- fine, resync below regardless
  }
  priceAlerts = priceAlerts.filter((a) => a.id !== btn.dataset.id);
  renderAlertsList();
});

document.getElementById("alert-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  const symbol = document.getElementById("alert-symbol").value;
  const condition = document.getElementById("alert-condition").value;
  const priceInput = document.getElementById("alert-price");
  const price = parseFloat(priceInput.value);
  if (!symbol || !price || price <= 0) return;
  const out = document.getElementById("alerts-list");
  try {
    const alert = await getJSON("/api/alerts", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, condition, price }),
    });
    priceAlerts.push(alert);
    renderAlertsList();
    priceInput.value = "";
  } catch (err) {
    out.innerHTML = `<p class="status-line error">Could not add alert: ${escapeHtml(err.message)}</p>` + out.innerHTML;
  }
});

function logAlertTrigger(message) {
  const log = document.getElementById("alerts-log");
  const line = document.createElement("p");
  line.className = "status-line alert-triggered";
  line.innerHTML = `<span class="live-dot"></span>${escapeHtml(message)}`;
  log.prepend(line);
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification("Aurum alert", { body: message });
  }
}

async function checkPriceAlerts(symbol, price) {
  const hits = priceAlerts.filter(
    (a) => a.symbol === symbol && ((a.condition === "above" && price >= a.price) || (a.condition === "below" && price <= a.price))
  );
  for (const alert of hits) {
    try {
      await getJSON(`/api/alerts/${alert.id}`, { method: "DELETE" });
    } catch (err) {
      // already removed some other way -- still worth reporting the trigger below
    }
    priceAlerts = priceAlerts.filter((a) => a.id !== alert.id);
    logAlertTrigger(`${symbol} ${alert.condition === "above" ? "crossed above" : "crossed below"} ${fmtPlain(alert.price)} — now ${fmtPlain(price)}`);
  }
  if (hits.length) renderAlertsList();
}

async function startLiveWatchlistLoop() {
  while (true) {
    await sleep(WATCHLIST_AUTO_REFRESH_INTERVAL_MS);
    await waitForVisible();
    await refreshWatchlistQuotes();
  }
}

// ---- symbol header: price, day change, "as of", and a dense key-stats row ---
// (quote fields -> Previous close/Open/Day's range/52wk range/Volume; the rest
// -- Market cap/P:E/EPS/Beta/Dividend/Earnings -- arrive later from Analyze,
// so the grid re-renders from whichever pieces have loaded so far.)

let currentSymbolQuote = null;
let currentSymbolFundamentals = null;
let currentSymbolEarnings = null;
let currentAnalysisReport = null; // the full /api/analyze response -- POSTed as-is to /api/narrative
let narrativeAvailable = false; // set once at boot from /api/narrative/status

let lastSymbolHeaderPrice = null;

function renderSymbolHeaderPrice(quote) {
  const priceEl = document.getElementById("symbol-price");
  const changeEl = document.getElementById("symbol-change");
  const asOfEl = document.getElementById("symbol-asof");
  if (!priceEl) return;
  priceEl.textContent = fmtPlain(quote.price);
  if (lastSymbolHeaderPrice !== null && lastSymbolHeaderPrice !== quote.price) {
    priceEl.classList.remove("flash-up", "flash-down");
    void priceEl.offsetWidth; // restart animation
    priceEl.classList.add(quote.price > lastSymbolHeaderPrice ? "flash-up" : "flash-down");
  }
  lastSymbolHeaderPrice = quote.price;
  if (changeEl) {
    if (quote.previous_close) {
      const change = quote.price - quote.previous_close;
      const changePct = (change / quote.previous_close) * 100;
      changeEl.className = change >= 0 ? "pos" : "neg";
      changeEl.textContent = `${fmtMoney(change)} (${fmtPct(changePct)})`;
    } else {
      changeEl.textContent = "";
    }
  }
  if (asOfEl) {
    asOfEl.textContent = quote.market_time ? "As of " + new Date(quote.market_time * 1000).toLocaleString() : "";
  }
}

function renderSymbolStats() {
  const out = document.getElementById("symbol-stats");
  if (!out) return;
  const q = currentSymbolQuote, f = currentSymbolFundamentals, e = currentSymbolEarnings;
  const stats = [];
  if (q) {
    stats.push(["Previous close", fmtPlain(q.previous_close)]);
    stats.push(["Open", fmtPlain(q.open)]);
    stats.push(["Day's range", `${fmtPlain(q.day_low)} – ${fmtPlain(q.day_high)}`]);
    stats.push(["52 week range", `${fmtPlain(q.fifty_two_week_low)} – ${fmtPlain(q.fifty_two_week_high)}`]);
    stats.push(["Volume", q.volume ? Math.round(q.volume).toLocaleString() : "—"]);
  }
  if (f) {
    stats.push(["Market cap", fmtMarketCap(f.market_cap_millions)]);
    stats.push(["P/E (TTM)", f.pe_ttm !== null && f.pe_ttm !== undefined ? f.pe_ttm.toFixed(2) : "—"]);
    stats.push(["EPS (TTM)", f.eps_ttm !== null && f.eps_ttm !== undefined ? fmtPlain(f.eps_ttm) : "—"]);
    stats.push(["Beta", f.beta !== null && f.beta !== undefined ? f.beta.toFixed(2) : "—"]);
    stats.push(["Dividend yield", f.dividend_yield_pct !== null && f.dividend_yield_pct !== undefined ? f.dividend_yield_pct.toFixed(2) + "%" : "—"]);
  }
  if (e) stats.push(["Earnings date", e.date]);
  out.innerHTML = stats
    .map(([label, value]) => `<div class="symbol-stat"><div class="symbol-stat-label">${escapeHtml(label)}</div><div class="symbol-stat-value">${escapeHtml(String(value))}</div></div>`)
    .join("");
}

async function loadSymbolQuote(name) {
  try {
    const quote = await getJSON(`/api/quote/${name}`);
    if (selectedSymbol !== name) return; // navigated away before this resolved
    currentSymbolQuote = quote;
    renderSymbolHeaderPrice(quote);
    renderSymbolStats();
  } catch (err) {
    // the header just stays at "—" -- not worth a scary error banner for one field,
    // and the live watchlist poll will fill it in on its next cycle regardless
  }
}

/** The whole point of this app's shape: click one stock, see everything.
 * Every panel below loads concurrently (none of these calls are awaited
 * here) rather than waiting on each other, and each one is a small,
 * independent fetch — a slow one (Kronos aside) doesn't block the rest. */
function openSymbol(name) {
  selectedSymbol = name;
  document.querySelectorAll("#watchlist-body tr").forEach((r) => r.classList.toggle("selected", r.dataset.name === name));
  switchView("symbol");

  document.getElementById("symbol-icon").innerHTML = iconFor(name);
  document.getElementById("symbol-title").textContent = name;
  document.getElementById("symbol-price").textContent = lastQuoteValues[name] !== undefined ? fmtPlain(lastQuoteValues[name]) : "—";
  document.getElementById("symbol-change").textContent = "";
  document.getElementById("symbol-asof").textContent = "";
  document.getElementById("symbol-verdict").innerHTML = "";
  document.getElementById("symbol-price").classList.remove("flash-up", "flash-down");
  currentSymbolQuote = null;
  currentSymbolFundamentals = null;
  currentSymbolEarnings = null;
  lastSymbolHeaderPrice = null;
  renderSymbolStats();

  loadChart(name);
  loadSymbolQuote(name);
  runScanner(name);
  runAnalyze(name);
  runDecision(name);
  runForecastBaseline(name);
  runBacktest(name);
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

let priceChart = null, candleSeries = null, priceLineSeries = null, baselineSeries = null, volumeSeries = null, emaSeries = null;

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
  // colored relative to the visible window's own opening price -- green above,
  // red below, filled -- the "is this period up or down" read Google Finance's
  // own quote page uses, which is what this chart type is styled after.
  // Lightweight Charts computes the fill gradient across the whole visible
  // price range of the pane, not just the local gap between the line and the
  // baseline -- for an instrument whose swings are small relative to that
  // full range, a gradient fading to transparent only ever shows its faint
  // end. Using the same color for both stops (an effectively solid fill)
  // sidesteps that instead of depending on how much of the pane the data fills.
  baselineSeries = priceChart.addBaselineSeries({
    topLineColor: cssVar("--positive"), topFillColor1: cssVar("--positive-area-fill"), topFillColor2: cssVar("--positive-area-fill"),
    bottomLineColor: cssVar("--negative"), bottomFillColor1: cssVar("--negative-area-fill"), bottomFillColor2: cssVar("--negative-area-fill"),
    lineWidth: 2, visible: false,
  });
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
  const baselineData = recent.map((b) => ({ time: b.timestamp, value: b.close }));
  baselineSeries.applyOptions({ baseValue: { type: "price", price: recent[0].close } });
  baselineSeries.setData(baselineData);
  volumeSeries.setData(
    recent.map((b) => ({ time: b.timestamp, value: b.volume, color: b.close >= b.open ? cssVar("--positive") : cssVar("--negative") }))
  );
  const emaValues = computeEMA(recent.map((b) => b.close), EMA_PERIOD);
  emaSeries.setData(recent.map((b, i) => ({ time: b.timestamp, value: emaValues[i] })));

  candleSeries.applyOptions({ visible: chartType === "candles" });
  priceLineSeries.applyOptions({ visible: chartType === "line" });
  baselineSeries.applyOptions({ visible: chartType === "area" });

  priceChart.timeScale().fitContent();
}

const CHART_LIVE_POLL_INTERVAL_MS = 15000; // same reasoning as WATCHLIST_AUTO_REFRESH_INTERVAL_MS above
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
  baselineSeries.update({ time: updated.timestamp, value: updated.close });
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

async function runScanner(name) {
  const out = document.getElementById("scanner-output");
  out.innerHTML = loadingHtml("Scanning…");
  try {
    const r = await getJSON(`/api/scan/${name}`);
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
}

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

function fmtMarketCap(millions) {
  if (millions === null || millions === undefined) return "—";
  if (millions >= 1e6) return "$" + (millions / 1e6).toFixed(2) + "T";
  if (millions >= 1e3) return "$" + (millions / 1e3).toFixed(1) + "B";
  return "$" + millions.toFixed(0) + "M";
}

function renderFundamentalsSection(f) {
  if (!f) return "";
  return `
    <h4 class="report-section-title">Fundamentals <span class="muted" style="font-weight:400">— via Finnhub, stock tickers only</span></h4>
    <div class="research-grid">
      <div class="research-stat"><div class="stat-label">P/E (TTM)</div><div class="stat-value">${f.pe_ttm !== null && f.pe_ttm !== undefined ? f.pe_ttm.toFixed(1) : "—"}</div></div>
      <div class="research-stat"><div class="stat-label">Market cap</div><div class="stat-value">${fmtMarketCap(f.market_cap_millions)}</div></div>
      <div class="research-stat"><div class="stat-label">EPS (TTM)</div><div class="stat-value">${f.eps_ttm !== null && f.eps_ttm !== undefined ? fmtPlain(f.eps_ttm) : "—"}</div></div>
      <div class="research-stat"><div class="stat-label">Dividend yield</div><div class="stat-value">${f.dividend_yield_pct !== null && f.dividend_yield_pct !== undefined ? f.dividend_yield_pct.toFixed(2) + "%" : "—"}</div></div>
      <div class="research-stat"><div class="stat-label">Net margin (TTM)</div><div class="stat-value">${f.net_profit_margin_pct !== null && f.net_profit_margin_pct !== undefined ? f.net_profit_margin_pct.toFixed(1) + "%" : "—"}</div></div>
      <div class="research-stat"><div class="stat-label">ROE (TTM)</div><div class="stat-value">${f.return_on_equity_pct !== null && f.return_on_equity_pct !== undefined ? f.return_on_equity_pct.toFixed(1) + "%" : "—"}</div></div>
      <div class="research-stat"><div class="stat-label">Beta</div><div class="stat-value">${f.beta !== null && f.beta !== undefined ? f.beta.toFixed(2) : "—"}</div></div>
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
  const verdictChangedHtml =
    r.previous_verdict && r.previous_verdict !== r.verdict
      ? `<div class="verdict-changed">Changed from <strong>${escapeHtml(r.previous_verdict)}</strong> since you last checked this symbol.</div>`
      : "";
  out.innerHTML = `
    <span class="verdict-pill verdict-${r.verdict}">${r.verdict}</span><span class="confidence-tag">${r.confidence} confidence</span>
    ${verdictChangedHtml}
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

    ${renderFundamentalsSection(r.fundamentals)}
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
      <div id="analyze-gauges" class="gauge-row" style="margin-top:10px"></div>
      ${r.risk.checks.map((c) => `<div class="check-row"><span class="${c.passed ? "check-pass" : "check-fail"}">${c.passed ? "✓" : "✗"}</span> <strong>${c.name}</strong> — ${escapeHtml(c.detail)}</div>`).join("")}
    </div>`;

  const gauges = document.getElementById("analyze-gauges");
  renderGauge(gauges, { label: "Position size", valuePct: r.risk.position_size_pct, limitPct: 1.0 });
  renderGauge(gauges, { label: "Exposure", valuePct: r.risk.exposure_pct, limitPct: 50.0 });
  renderGauge(gauges, { label: "Drawdown", valuePct: r.risk.drawdown_pct, limitPct: 20.0 });
}

async function runAnalyze(name) {
  const out = document.getElementById("analyze-output");
  out.innerHTML = loadingHtml(`Analyzing ${escapeHtml(name)}…`);
  try {
    const r = await getJSON(`/api/analyze/${name}`);
    renderAnalysis(r);
    if (selectedSymbol === name) {
      document.getElementById("symbol-verdict").innerHTML = `<span class="verdict-pill verdict-${r.verdict}">${r.verdict}</span>`;
      currentSymbolFundamentals = r.fundamentals;
      currentSymbolEarnings = r.earnings;
      renderSymbolStats();
      currentAnalysisReport = r;
      const narrativeSection = document.getElementById("ai-narrative-section");
      const narrativeOutput = document.getElementById("narrative-output");
      if (narrativeSection) narrativeSection.hidden = !narrativeAvailable;
      if (narrativeOutput) narrativeOutput.innerHTML = "";
    }
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
}

// ---- AI narrative (optional -- only shows once a Groq key is configured) ----

async function loadNarrativeAvailability() {
  try {
    const r = await getJSON("/api/narrative/status");
    narrativeAvailable = r.available;
  } catch (err) {
    narrativeAvailable = false;
  }
}

document.getElementById("run-narrative").addEventListener("click", async () => {
  if (!currentAnalysisReport || !selectedSymbol) return;
  const out = document.getElementById("narrative-output");
  out.innerHTML = loadingHtml("Writing summary…");
  try {
    const r = await getJSON(`/api/narrative/${selectedSymbol}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(currentAnalysisReport),
    });
    out.innerHTML = `<div class="narrative-text">${escapeHtml(r.narrative)}</div>`;
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- decision memo -----------------------------------------------------------
// A second, independent verdict (single fixed 2R target, different scoring)
// alongside the Detailed Analysis above — the risk checklist and gauges are
// already shown there, so this stays compact rather than repeating them.

async function runDecision(name) {
  const out = document.getElementById("decision-output");
  out.innerHTML = loadingHtml("Building memo…");
  try {
    const r = await getJSON(`/api/decision/${name}`);
    out.innerHTML =
      `<span class="verdict-pill verdict-${r.verdict}">${r.verdict}</span>  <span class="verdict-pill verdict-${r.risk.passed ? "APPROVED" : "REJECTED"}">${r.risk.status}</span>\n\n` +
      `Plan: ${r.plan.direction.toUpperCase()}  entry ${fmtPlain(r.plan.entry)}  stop ${fmtPlain(r.plan.stop)}  target ${fmtPlain(r.plan.target)}  R:R ${r.plan.risk_reward_ratio.toFixed(2)}\n` +
      `Setup: ${r.scan.pattern} (${r.scan.score_pct.toFixed(0)}% confirmed)\n\n` +
      (r.reasons.length ? "Why:\n" + r.reasons.map((x) => "  • " + escapeHtml(x)).join("\n") : "All checks clear.");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
}

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

// ---- correlation (heatmap, auto-loads whenever the Optimizer view opens) ----

function renderCorrelationGrid(symbols, matrix) {
  const cellStyle = (v) => {
    const pct = Math.round(Math.abs(v) * 70); // capped so the number underneath stays legible
    const color = v >= 0 ? "var(--positive)" : "var(--negative)";
    return `background: color-mix(in srgb, ${color} ${pct}%, var(--surface-2));`;
  };
  const header = `<th></th>` + symbols.map((s) => `<th>${escapeHtml(s)}</th>`).join("");
  const rows = symbols
    .map(
      (rowSym, i) =>
        `<tr><th>${escapeHtml(rowSym)}</th>` +
        matrix[i].map((v) => `<td class="corr-cell" style="${cellStyle(v)}">${v.toFixed(2)}</td>`).join("") +
        `</tr>`
    )
    .join("");
  return `<div class="table-scroll"><table class="corr-table"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function loadCorrelation() {
  const out = document.getElementById("correlation-output");
  out.innerHTML = loadingHtml("Computing correlations…");
  try {
    const r = await getJSON("/api/correlation");
    let html = renderCorrelationGrid(r.symbols, r.matrix);
    if (r.skipped_symbols.length) {
      html += `<p class="status-line" style="margin-top:10px">Skipped (no data): ${escapeHtml(r.skipped_symbols.join(", "))}</p>`;
    }
    out.innerHTML = html;
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
}

// ---- forecast ---------------------------------------------------------------

async function runForecastBaseline(name) {
  const out = document.getElementById("forecast-output");
  out.innerHTML = loadingHtml("Forecasting…");
  try {
    const r = await getJSON(`/api/forecast/baseline/${name}?horizon=10`);
    const lines = [`Method: ${r.method}`, ""];
    r.point_forecast.forEach((p, i) => {
      lines.push(`  +${i + 1}d   ${fmtPlain(p).padStart(10)}   [${fmtPlain(r.lower_band[i])} .. ${fmtPlain(r.upper_band[i])}]`);
    });
    lines.push("", r.note);
    out.textContent = lines.join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
}

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

async function runBacktest(name) {
  const out = document.getElementById("backtest-output");
  out.innerHTML = loadingHtml("Running backtest on real history…");
  try {
    const r = await getJSON(`/api/backtest/${name}`);
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
}

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

// ---- markets board (live global index dashboard) ---------------------------
// The server owns the upstream fetching and caches the batched snapshot for a
// few seconds, so polling here at 5s costs one Yahoo call per 5s no matter how
// many tabs are open -- see the markets section of aurum/web/server.py.

let marketsRefreshMs = 5000;
let marketsRows = [];
let marketsAsOf = null;
let marketsStale = false;
let marketsDegraded = false;
let marketsWarmingUp = false;
let marketsLastError = null;
const marketsLastPrice = {}; // ticker -> last seen price, for the tick flash

function marketsDirClass(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "mkt-flat";
  if (v > 0) return "mkt-up";
  if (v < 0) return "mkt-down";
  return "mkt-flat";
}
function marketsArrow(v) {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "";
  return `<span class="mkt-arrow">${v > 0 ? "▲" : "▼"}</span>`;
}
/** Magnitude only — the ▲/▼ arrow beside it already carries the sign, so
 * printing "▼−232.46" would mark the same negative twice. */
function fmtMagnitude(n, decimals = 2, suffix = "") {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
}
function fmtIndexValue(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtMarketTime(unixSeconds) {
  if (!unixSeconds) return "—";
  return new Date(unixSeconds * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** A tiny inline sparkline from the last ~30 daily closes. Coloured by the
 * period's own direction (first vs last), which is not always the same sign
 * as today's move -- an index can be up on the day inside a falling month. */
function sparklineSvg(values, width = 150, height = 34) {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const pts = values.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 4) - 2]);
  const line = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const area = line + ` L${width},${height} L0,${height} Z`;
  const up = values[values.length - 1] >= values[0];
  const stroke = up ? "var(--positive)" : "var(--negative)";
  const fill = up ? "var(--positive-area-fill)" : "var(--negative-area-fill)";
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">` +
    `<path d="${area}" fill="${fill}" stroke="none"/>` +
    `<path d="${line}" fill="none" stroke="${stroke}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>` +
    `</svg>`;
}

function renderMarketsCards() {
  const el = document.getElementById("markets-cards");
  if (!el) return;
  // The headline strip: one card per major benchmark, in board order.
  const featured = marketsRows.filter((r) => r.region !== "Commodities & FX").slice(0, 10);
  el.innerHTML = featured.map((r) => {
    const cls = marketsDirClass(r.change_pct);
    return `<div class="mkt-card">` +
      `<span class="mkt-card-code">${escapeHtml(r.code)}</span>` +
      `<span class="mkt-card-name" title="${escapeHtml(r.label)}">${escapeHtml(r.label)}</span>` +
      `<span class="mkt-card-value">${fmtIndexValue(r.price)}<span class="mkt-ccy">${escapeHtml(r.currency)}</span></span>` +
      `<span class="mkt-card-chg ${cls}">${marketsArrow(r.change_pct)}${fmtMagnitude(r.change_pct, 2, "%")}</span>` +
      // The number above is today's move; the sparkline is the last 30 daily
      // closes, so the two legitimately disagree (an index can be down today
      // inside a rising month). Label the period so that reads as intended
      // rather than as a mismatched colour.
      `<div class="mkt-card-spark">${sparklineSvg(r.spark)}<span class="mkt-spark-tag">30D</span></div>` +
      `</div>`;
  }).join("");
}

/** Squarified treemap. Boxes are weighted by 1 + |move| so a flat day still
 * produces readable tiles instead of slivers, and coloured by the size of the
 * move in each direction. */
function squarify(items, x, y, w, h, out) {
  if (!items.length) return;
  if (items.length === 1) {
    out.push({ ...items[0], x, y, w, h });
    return;
  }
  const total = items.reduce((s, i) => s + i.weight, 0);
  let split = 0, acc = 0;
  const half = total / 2;
  while (split < items.length - 1 && acc + items[split].weight <= half) {
    acc += items[split].weight;
    split++;
  }
  const headItems = items.slice(0, split || 1);
  const tailItems = items.slice(split || 1);
  const headWeight = headItems.reduce((s, i) => s + i.weight, 0);
  const frac = total ? headWeight / total : 0.5;
  if (w >= h) {
    const wa = w * frac;
    squarify(headItems, x, y, wa, h, out);
    squarify(tailItems, x + wa, y, w - wa, h, out);
  } else {
    const ha = h * frac;
    squarify(headItems, x, y, w, ha, out);
    squarify(tailItems, x, y + ha, w, h - ha, out);
  }
}

function renderMarketsTreemap() {
  const el = document.getElementById("markets-treemap");
  if (!el) return;
  const rows = marketsRows.filter((r) => r.change_pct !== null && r.change_pct !== undefined).slice(0, 18);
  if (!rows.length) { el.innerHTML = ""; return; }

  const items = rows
    .map((r) => ({ code: r.code, pct: r.change_pct, weight: 1 + Math.abs(r.change_pct) * 1.6 }))
    .sort((a, b) => b.weight - a.weight);

  const W = 340, H = 300;
  const boxes = [];
  squarify(items, 0, 0, W, H, boxes);

  // Opacity carries magnitude; 1.5% is treated as a "big" day for an index,
  // so anything at or past it saturates rather than scaling off a daily max
  // (which would make a calm day look identical to a violent one).
  const cells = boxes.map((b) => {
    const mag = Math.min(Math.abs(b.pct) / 1.5, 1);
    const opacity = (0.20 + mag * 0.62).toFixed(2);
    const fill = b.pct >= 0 ? "var(--positive)" : "var(--negative)";
    const showLabel = b.w > 46 && b.h > 26;
    const showPct = b.w > 60 && b.h > 42;
    const cx = b.x + b.w / 2;
    return `<g>` +
      `<rect x="${b.x.toFixed(1)}" y="${b.y.toFixed(1)}" width="${Math.max(0, b.w - 2).toFixed(1)}" height="${Math.max(0, b.h - 2).toFixed(1)}" ` +
      `rx="4" fill="${fill}" fill-opacity="${opacity}"/>` +
      (showLabel
        ? `<text x="${cx.toFixed(1)}" y="${(b.y + b.h / 2 - (showPct ? 5 : 0)).toFixed(1)}" text-anchor="middle" dominant-baseline="middle" ` +
          `font-size="10" font-weight="700" fill="var(--text-primary)">${escapeHtml(b.code)}</text>`
        : "") +
      (showPct
        ? `<text x="${cx.toFixed(1)}" y="${(b.y + b.h / 2 + 9).toFixed(1)}" text-anchor="middle" dominant-baseline="middle" ` +
          `font-size="9" fill="var(--text-secondary)">${(b.pct > 0 ? "+" : "") + b.pct.toFixed(2)}%</text>`
        : "") +
      `<title>${escapeHtml(b.code)} ${(b.pct > 0 ? "+" : "") + b.pct.toFixed(2)}%</title>` +
      `</g>`;
  }).join("");

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${cells}</svg>`;
}

function renderMarketsRegions() {
  const wrap = document.getElementById("markets-regions");
  if (!wrap) return;
  if (!marketsRows.length) {
    if (marketsWarmingUp && !marketsLastError) {
      wrap.innerHTML =
        `<div class="card">${loadingHtml("Fetching the global board — this can take a few seconds on first load.")}</div>`;
      return;
    }
    // Most likely cause by far is Yahoo rate-limiting this IP, so say that
    // plainly instead of leaving an empty panel that looks broken.
    wrap.innerHTML =
      `<div class="card"><div class="panel-body">` +
      `No market data yet.\n\n` +
      `${escapeHtml(marketsLastError || "The upstream feed did not return any instruments.")}\n\n` +
      `Yahoo rate-limits by IP, and this board covers the whole global index list. If the terminal ` +
      `has been polling hard, give it a few minutes — the board keeps retrying and comes back on its own.` +
      `</div></div>`;
    return;
  }
  const regions = [...new Set(marketsRows.map((r) => r.region))];
  wrap.innerHTML = regions.map((region) => {
    const rows = marketsRows.filter((r) => r.region === region);
    const body = rows.map((r) => {
      const dayCls = marketsDirClass(r.change_pct);
      return `<tr data-ticker="${escapeHtml(r.ticker)}">` +
        `<td class="mkt-name-cell"><div class="mkt-name-code">${escapeHtml(r.code)}</div>` +
        `<div class="mkt-name-label">${escapeHtml(r.label)}</div></td>` +
        `<td class="c-num">${fmtIndexValue(r.price)}</td>` +
        `<td class="c-num ${dayCls}">${marketsArrow(r.change)}${fmtMagnitude(r.change)}</td>` +
        `<td class="c-num ${dayCls}">${marketsArrow(r.change_pct)}${fmtMagnitude(r.change_pct, 2, "%")}</td>` +
        `<td class="c-num ${marketsDirClass(r.change_1m_pct)}">${marketsArrow(r.change_1m_pct)}${fmtMagnitude(r.change_1m_pct, 2, "%")}</td>` +
        `<td class="c-num ${marketsDirClass(r.change_1y_pct)}">${marketsArrow(r.change_1y_pct)}${fmtMagnitude(r.change_1y_pct, 2, "%")}</td>` +
        `<td class="mkt-time">${fmtMarketTime(r.market_time)}</td>` +
        `</tr>`;
    }).join("");
    return `<div class="card"><div class="card-head"><h3 class="mkt-region-title">${escapeHtml(region)}</h3></div>` +
      `<div class="table-scroll"><table class="mkt-table"><thead><tr>` +
      `<th>Name</th><th class="c-num">Value</th><th class="c-num">Change</th><th class="c-num">% Change</th>` +
      `<th class="c-num">1 Month</th><th class="c-num">1 Year</th><th class="mkt-time">Time</th>` +
      `</tr></thead><tbody>${body}</tbody></table></div></div>`;
  }).join("");
}

function flashMarketTicks() {
  marketsRows.forEach((r) => {
    const prev = marketsLastPrice[r.ticker];
    if (prev !== undefined && r.price !== null && r.price !== prev) {
      const row = document.querySelector(`#markets-regions tr[data-ticker="${CSS.escape(r.ticker)}"]`);
      if (row) {
        const cls = r.price > prev ? "mkt-tick-up" : "mkt-tick-down";
        row.classList.remove("mkt-tick-up", "mkt-tick-down");
        void row.offsetWidth; // restart the animation
        row.classList.add(cls);
      }
    }
    if (r.price !== null && r.price !== undefined) marketsLastPrice[r.ticker] = r.price;
  });
}

function renderMarketsStatus() {
  const el = document.getElementById("markets-status");
  if (!el) return;
  if (marketsLastError && !marketsRows.length) {
    el.innerHTML = `<span class="live-dot error"></span>${escapeHtml(marketsLastError)}`;
    el.classList.add("error");
    return;
  }
  el.classList.remove("error");
  if (marketsWarmingUp && !marketsRows.length) {
    el.innerHTML = `<span class="spinner"></span>Fetching the global board…`;
    return;
  }
  const age = marketsAsOf ? Math.max(0, Math.round(Date.now() / 1000 - marketsAsOf)) : null;
  const paused = marketsRefreshMs === 0;
  const dot = paused ? "" : `<span class="live-dot${marketsStale ? " error" : ""}"></span>`;
  const label = paused
    ? "Auto-refresh off"
    : `Live · ${marketsRows.length} instruments · updated ${age === null ? "—" : age + "s"} ago`;
  const badges =
    (marketsStale ? `<span class="markets-stale-badge">stale — last refresh failed</span>` : "") +
    (marketsDegraded ? `<span class="markets-stale-badge">slow mode — batch feed unavailable, prices refresh every few minutes</span>` : "");
  el.innerHTML = `${dot}${escapeHtml(label)} ${badges}`;
}

async function loadMarkets() {
  try {
    const data = await getJSON("/api/markets");
    marketsRows = data.rows || [];
    marketsAsOf = data.as_of;
    marketsStale = !!data.stale;
    marketsDegraded = !!data.degraded;
    marketsWarmingUp = !!data.warming_up;
    marketsLastError = null;
    renderMarketsCards();
    renderMarketsTreemap();
    renderMarketsRegions();
    flashMarketTicks();
  } catch (err) {
    marketsLastError = err.message;
    // Only repaint the board as "empty" if we have nothing to show. If a
    // previous snapshot is still on screen, leave it up -- a stale board
    // beats a blank one -- and let the status line carry the failure.
    if (!marketsRows.length) renderMarketsRegions();
  }
  renderMarketsStatus();
}

async function startMarketsLoop() {
  for (;;) {
    if (marketsRefreshMs === 0) { await sleep(500); continue; }
    await sleep(marketsRefreshMs);
    await waitForVisible();
    // Only poll while the board is actually on screen — no point spending
    // upstream budget refreshing a tab the user isn't looking at.
    if (!document.getElementById("view-markets").classList.contains("active")) continue;
    if (marketsRefreshMs === 0) continue;
    await loadMarkets();
  }
}

document.getElementById("markets-interval").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-ms]");
  if (!btn) return;
  marketsRefreshMs = parseInt(btn.dataset.ms, 10);
  document.querySelectorAll("#markets-interval button").forEach((b) => b.classList.toggle("active", b === btn));
  renderMarketsStatus();
  if (marketsRefreshMs) loadMarkets();
});

// ---- journal (paper-trading log, merged in from the standalone Bullion Ledger
//      page -- persisted server-side now via /api/journal instead of localStorage) --

const JOURNAL_SETUP_LABELS = {
  none: "No defined setup",
  support: "Support / swing low",
  ma: "Pullback to MA",
  rsi: "RSI oversold bounce",
  breakout: "Breakout",
  news: "News / catalyst",
  other: "Other",
};

let journalState = { starting_equity: 3000, trades: [] };
let journalSelectedDir = "long";
let journalSelectedConf = null;
let journalSelectedTrend = null;

function journalOutcomeOf(t) {
  const p = Number(t.pnl) || 0;
  if (p > 0.004) return "win";
  if (p < -0.004) return "loss";
  return "breakeven";
}
function journalRMultiple(t) {
  const risk = Number(t.risk);
  if (!risk || risk <= 0) return null;
  return (Number(t.pnl) || 0) / risk;
}
function journalMean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}
function journalFmtDateShort(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function journalSortedChrono() {
  return journalState.trades.slice().sort((a, b) => new Date(a.ts) - new Date(b.ts));
}
function journalSortedRecent() {
  return journalState.trades.slice().sort((a, b) => new Date(b.ts) - new Date(a.ts));
}

function journalComputeStats() {
  const trades = journalState.trades;
  const total = trades.length;
  const startEquity = journalState.starting_equity;
  let pnlSum = 0;
  const wins = [], losses = [];
  for (const t of trades) {
    const p = Number(t.pnl) || 0;
    pnlSum += p;
    const o = journalOutcomeOf(t);
    if (o === "win") wins.push(p);
    else if (o === "loss") losses.push(p);
  }
  const equity = startEquity + pnlSum;
  const returnPct = (equity - startEquity) / startEquity * 100;
  const winRate = total ? (wins.length / total * 100) : 0;
  let best = null, worst = null;
  for (const t of trades) {
    const p = Number(t.pnl) || 0;
    if (best === null || p > best.pnl) best = t;
    if (worst === null || p < worst.pnl) worst = t;
  }
  return {
    total, equity, returnPct, winRate, startEquity,
    avgWin: journalMean(wins), avgLoss: journalMean(losses),
    expectancy: total ? pnlSum / total : 0,
    best, worst, wins: wins.length, losses: losses.length,
  };
}

function renderJournalStats() {
  const s = journalComputeStats();
  const el = document.getElementById("journal-stats");
  if (!el) return;
  const equityUp = s.equity >= s.startEquity;
  const tiles = [
    { label: "Equity", value: fmtPlain(s.equity), sub: fmtPct(s.returnPct) + " since " + fmtPlain(s.startEquity), subClass: equityUp ? "pos" : "neg", hero: true },
    { label: "Win rate", value: s.total ? s.winRate.toFixed(0) + "%" : "—", sub: s.total ? `${s.wins}W / ${s.losses}L` : "no trades yet" },
    { label: "Avg win / avg loss", value: (s.wins ? fmtMoney(s.avgWin) : "—") + " / " + (s.losses ? fmtMoney(s.avgLoss) : "—"), sub: "per trade" },
    { label: "Expectancy", value: fmtMoney(s.expectancy), sub: "avg P&L per trade", subClass: s.expectancy >= 0 ? "pos" : "neg" },
    { label: "Best / worst trade", value: (s.best ? fmtMoney(s.best.pnl) : "—") + " / " + (s.worst ? fmtMoney(s.worst.pnl) : "—"), sub: "single trade" },
    { label: "Trades logged", value: String(s.total), sub: s.total ? "all time" : "log your first below" },
  ];
  el.innerHTML = tiles.map((t) =>
    `<div class="stat-tile${t.hero ? " hero" : ""}">` +
    `<span class="stat-label">${escapeHtml(t.label)}</span>` +
    `<span class="stat-value">${t.value}</span>` +
    `<span class="stat-sub${t.subClass ? " " + t.subClass : ""}">${escapeHtml(t.sub)}</span>` +
    `</div>`
  ).join("");
}

function renderJournalChart() {
  const wrap = document.getElementById("journal-chart-wrap");
  const subtitle = document.getElementById("journal-chart-subtitle");
  if (!wrap) return;
  const chrono = journalSortedChrono();
  const startEquity = journalState.starting_equity;
  if (subtitle) {
    if (chrono.length) {
      const first = journalFmtDateShort(chrono[0].ts).split(",")[0];
      const last = journalFmtDateShort(chrono[chrono.length - 1].ts).split(",")[0];
      subtitle.textContent = `Since ${fmtPlain(startEquity)} starting balance · ${first} – ${last} · ${chrono.length} trade${chrono.length === 1 ? "" : "s"}`;
    } else {
      subtitle.textContent = `Since ${fmtPlain(startEquity)} starting balance`;
    }
  }
  if (!chrono.length) {
    wrap.innerHTML = '<div class="chart-empty">No trades logged yet — your equity curve starts as soon as you add one below.</div>';
    return;
  }
  const points = [{ equity: startEquity, ts: null, pnl: 0 }];
  let running = startEquity;
  for (const t of chrono) {
    running += Number(t.pnl) || 0;
    points.push({ equity: running, ts: t.ts, pnl: t.pnl, instrument: t.instrument });
  }
  const W = 640, H = 220, padL = 56, padR = 70, padT = 16, padB = 16;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const values = points.map((p) => p.equity);
  let minV = Math.min(...values), maxV = Math.max(...values);
  if (minV === maxV) { minV -= 10; maxV += 10; }
  let range = maxV - minV;
  minV -= range * 0.08; maxV += range * 0.08;
  range = maxV - minV;

  const xAt = (i) => padL + (points.length === 1 ? plotW : (i / (points.length - 1)) * plotW);
  const yAt = (v) => padT + plotH - ((v - minV) / range) * plotH;

  const coords = points.map((p, i) => ({ x: xAt(i), y: yAt(p.equity), p }));
  const linePath = coords.map((c, i) => (i === 0 ? "M" : "L") + c.x.toFixed(2) + "," + c.y.toFixed(2)).join(" ");
  const baseline = padT + plotH;
  const areaPath = linePath + " L" + coords[coords.length - 1].x.toFixed(2) + "," + baseline + " L" + coords[0].x.toFixed(2) + "," + baseline + " Z";

  const gridSteps = [minV + range * 0.15, minV + range * 0.5, minV + range * 0.85];
  const gridLines = gridSteps.map((v) => {
    const y = yAt(v).toFixed(2);
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="var(--border)" stroke-width="1" />` +
      `<text x="${padL - 8}" y="${y}" text-anchor="end" dominant-baseline="middle" font-family="var(--font-mono)" font-size="10" fill="var(--text-muted)">${fmtPlain(v)}</text>`;
  }).join("");

  const last = coords[coords.length - 1];
  const endLabel = fmtPlain(last.p.equity);

  wrap.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity curve, currently ${endLabel}">` +
    gridLines +
    `<path d="${areaPath}" fill="var(--accent)" fill-opacity="0.10" stroke="none" />` +
    `<path d="${linePath}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />` +
    `<circle cx="${last.x.toFixed(2)}" cy="${last.y.toFixed(2)}" r="5" fill="var(--accent)" stroke="var(--surface-solid)" stroke-width="2" />` +
    `<text x="${(last.x + 9).toFixed(2)}" y="${(last.y - 6).toFixed(2)}" font-family="var(--font-mono)" font-size="12" font-weight="600" fill="var(--text-primary)">${endLabel}</text>` +
    `<line id="journal-hover-line" x1="0" y1="${padT}" x2="0" y2="${baseline}" stroke="var(--text-muted)" stroke-width="1" opacity="0" />` +
    `<circle id="journal-hover-dot" r="4.5" fill="var(--accent)" stroke="var(--surface-solid)" stroke-width="2" opacity="0" />` +
    `<rect id="journal-hover-capture" x="${padL}" y="0" width="${plotW}" height="${H}" fill="transparent" />` +
    `</svg>` +
    `<div class="chart-tooltip" id="journal-chart-tooltip"></div>`;

  const svgEl = wrap.querySelector("svg");
  const hoverLine = document.getElementById("journal-hover-line");
  const hoverDot = document.getElementById("journal-hover-dot");
  const capture = document.getElementById("journal-hover-capture");
  const tooltip = document.getElementById("journal-chart-tooltip");

  function nearestIndex(clientX) {
    const rect = svgEl.getBoundingClientRect();
    const scaleX = W / rect.width;
    const localX = (clientX - rect.left) * scaleX;
    let best = 0, bestDist = Infinity;
    coords.forEach((c, i) => { const d = Math.abs(c.x - localX); if (d < bestDist) { bestDist = d; best = i; } });
    return best;
  }
  function showAt(i) {
    const c = coords[i];
    hoverLine.setAttribute("x1", c.x); hoverLine.setAttribute("x2", c.x); hoverLine.setAttribute("opacity", "1");
    hoverDot.setAttribute("cx", c.x); hoverDot.setAttribute("cy", c.y); hoverDot.setAttribute("opacity", "1");
    const rect = svgEl.getBoundingClientRect();
    const scale = rect.width / W;
    tooltip.style.left = (c.x * scale) + "px";
    tooltip.style.top = (c.y * scale - 10) + "px";
    const label = i === 0 ? "Start" : journalFmtDateShort(c.p.ts);
    const pnlLine = i === 0 ? "" : `<div class="tt-pnl ${c.p.pnl >= 0 ? "pos" : "neg"}">${c.p.instrument ? escapeHtml(c.p.instrument) + " · " : ""}${fmtMoney(c.p.pnl)}</div>`;
    tooltip.innerHTML = `<div>${escapeHtml(label)}</div><div>${fmtPlain(c.p.equity)}</div>${pnlLine}`;
    tooltip.classList.add("visible");
  }
  function hide() { hoverLine.setAttribute("opacity", "0"); hoverDot.setAttribute("opacity", "0"); tooltip.classList.remove("visible"); }
  capture.addEventListener("mousemove", (e) => showAt(nearestIndex(e.clientX)));
  capture.addEventListener("mouseleave", hide);
  capture.addEventListener("touchstart", (e) => { if (e.touches && e.touches[0]) showAt(nearestIndex(e.touches[0].clientX)); }, { passive: true });
}

function renderJournalSetupBreakdown() {
  const tbody = document.getElementById("journal-setup-tbody");
  if (!tbody) return;
  const groups = {};
  journalState.trades.forEach((t) => {
    const key = t.setup || "none";
    if (!groups[key]) groups[key] = { count: 0, wins: 0, total: 0 };
    groups[key].count++;
    groups[key].total += Number(t.pnl) || 0;
    if (journalOutcomeOf(t) === "win") groups[key].wins++;
  });
  const rows = Object.keys(groups).map((key) => {
    const g = groups[key];
    return { key, label: JOURNAL_SETUP_LABELS[key] || key, count: g.count, winRate: g.count ? (g.wins / g.count * 100) : 0, total: g.total, avg: g.count ? g.total / g.count : 0 };
  }).sort((a, b) => b.total - a.total);
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="detail-empty">No trades logged yet.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r) =>
    `<tr><td>${escapeHtml(r.label)}</td>` +
    `<td class="c-num">${r.count}</td>` +
    `<td class="c-num">${r.winRate.toFixed(0)}%</td>` +
    `<td class="c-num ${r.total >= 0 ? "outcome-win" : "outcome-loss"}">${fmtMoney(r.total)}</td>` +
    `<td class="c-num ${r.avg >= 0 ? "outcome-win" : "outcome-loss"}">${fmtMoney(r.avg)}</td></tr>`
  ).join("");
}

function renderJournalTradeTable() {
  const tbody = document.getElementById("journal-trade-tbody");
  const countEl = document.getElementById("journal-trade-count");
  if (!tbody) return;
  const trades = journalSortedRecent();
  if (countEl) countEl.textContent = `${trades.length} trade${trades.length === 1 ? "" : "s"}`;
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="detail-empty">Nothing logged yet — add your first trade above.</td></tr>';
    return;
  }
  tbody.innerHTML = trades.map((t) => {
    const outcome = journalOutcomeOf(t);
    const r = journalRMultiple(t);
    const rDisplay = r === null ? "—" : (r >= 0 ? "+" : "") + r.toFixed(2) + "R";
    const setupLabel = JOURNAL_SETUP_LABELS[t.setup] || t.setup || "—";
    const entryExit = (t.entry != null && t.exit != null) ? `${t.entry} → ${t.exit}` : "—";
    const notesHtml = t.notes ? escapeHtml(t.notes) : '<span class="detail-empty">No notes recorded.</span>';
    const confHtml = t.confidence ? `${t.confidence} / 5` : "—";
    const riskHtml = t.risk != null && t.risk !== "" ? fmtPlain(t.risk) : "—";
    const emaHtml = t.ema != null ? fmtPlain(t.ema) : "—";
    const trendHtml = t.trend === "up" ? "Up" : t.trend === "down" ? "Down" : t.trend === "flat" ? "Flat" : "—";
    let emaDistHtml = "—";
    if (t.entry != null && t.ema != null) {
      const emaDist = t.entry - t.ema;
      emaDistHtml = (emaDist >= 0 ? "+" : "") + emaDist.toFixed(2) + (emaDist >= 0 ? " above EMA" : " below EMA");
    }
    return `<tr class="trade-row" data-id="${t.id}">` +
      `<td>${escapeHtml(journalFmtDateShort(t.ts))}</td>` +
      `<td>${escapeHtml(t.instrument)}</td>` +
      `<td><span class="dir-pill dir-${t.direction}">${t.direction === "long" ? "Long" : "Short"}</span></td>` +
      `<td class="c-num">${t.size != null ? t.size : "—"}</td>` +
      `<td class="c-num">${escapeHtml(entryExit)}</td>` +
      `<td><span class="setup-chip">${escapeHtml(setupLabel)}</span></td>` +
      `<td class="c-num">${rDisplay}</td>` +
      `<td class="c-num outcome-${outcome}">${fmtMoney(t.pnl)}</td>` +
      `<td><span class="pill pill-${outcome}">${outcome.toUpperCase()}</span></td>` +
      `<td><button type="button" class="row-edit-btn" data-toggle="${t.id}" aria-expanded="false" style="width:auto;padding:4px 8px;font-size:0.72rem">Notes</button></td>` +
      `<td><button type="button" class="row-delete-btn" data-delete="${t.id}" aria-label="Delete trade" style="width:26px">✕</button></td>` +
      `</tr>` +
      `<tr class="journal-detail" id="journal-detail-${t.id}" hidden><td colspan="11"><div class="journal-detail-inner">` +
      `<div><span class="detail-label">Notes</span>${notesHtml}</div>` +
      `<div><span class="detail-label">Confidence</span>${confHtml}</div>` +
      `<div><span class="detail-label">Risked</span>${riskHtml}</div>` +
      `<div><span class="detail-label">50 EMA</span>${emaHtml}</div>` +
      `<div><span class="detail-label">Trend</span>${trendHtml}</div>` +
      `<div><span class="detail-label">Entry vs EMA</span>${emaDistHtml}</div>` +
      `</div></td></tr>`;
  }).join("");

  tbody.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = document.getElementById("journal-detail-" + btn.getAttribute("data-toggle"));
      if (!row) return;
      const open = !row.hidden;
      row.hidden = open;
      btn.setAttribute("aria-expanded", String(!open));
      btn.textContent = open ? "Notes" : "Hide";
    });
  });
  tbody.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-delete");
      if (!window.confirm("Delete this trade from the ledger?")) return;
      journalState = await getJSON(`/api/journal/${id}`, { method: "DELETE" });
      renderJournal();
    });
  });
}

function renderJournal() {
  renderJournalStats();
  renderJournalChart();
  renderJournalSetupBreakdown();
  renderJournalTradeTable();
}

async function loadJournal() {
  journalState = await getJSON("/api/journal");
  renderJournal();
}

function wireJournalDirToggle() {
  const group = document.getElementById("jf-dir-group");
  if (!group) return;
  group.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      journalSelectedDir = btn.getAttribute("data-dir");
      group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    });
  });
}
function wireJournalConfidence() {
  const group = document.getElementById("jf-conf-group");
  if (!group) return;
  group.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const val = btn.getAttribute("data-conf");
      journalSelectedConf = journalSelectedConf === val ? null : val;
      group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-conf") === journalSelectedConf)));
    });
  });
}
function wireJournalTrend() {
  const group = document.getElementById("jf-trend-group");
  if (!group) return;
  group.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const val = btn.getAttribute("data-trend");
      journalSelectedTrend = journalSelectedTrend === val ? null : val;
      group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-trend") === journalSelectedTrend)));
    });
  });
}
function journalSetDefaultDate() {
  const input = document.getElementById("jf-date");
  if (!input) return;
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  input.value = now.toISOString().slice(0, 16);
}
function journalResetTicket(form) {
  form.reset();
  journalSelectedDir = "long";
  journalSelectedConf = null;
  journalSelectedTrend = null;
  document.getElementById("jf-dir-group").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-dir") === "long")));
  document.getElementById("jf-conf-group").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
  document.getElementById("jf-trend-group").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
  journalSetDefaultDate();
}

function wireJournalForm() {
  const form = document.getElementById("journal-form");
  if (!form) return;
  wireJournalDirToggle();
  wireJournalConfidence();
  wireJournalTrend();
  journalSetDefaultDate();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const instrument = document.getElementById("jf-instrument").value.trim();
    const dateVal = document.getElementById("jf-date").value;
    const pnlVal = document.getElementById("jf-pnl").value;
    if (!instrument || !dateVal || pnlVal === "") return;
    const numOrNull = (id) => { const v = document.getElementById(id).value; return v === "" ? null : Number(v); };
    const payload = {
      ts: new Date(dateVal).toISOString(),
      instrument,
      direction: journalSelectedDir,
      entry: numOrNull("jf-entry"),
      exit: numOrNull("jf-exit"),
      size: numOrNull("jf-size"),
      pnl: Number(pnlVal),
      risk: numOrNull("jf-risk"),
      ema: numOrNull("jf-ema"),
      trend: journalSelectedTrend,
      setup: document.getElementById("jf-setup").value,
      notes: document.getElementById("jf-notes").value.trim(),
      confidence: journalSelectedConf ? Number(journalSelectedConf) : null,
    };
    journalState = await getJSON("/api/journal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderJournal();
    journalResetTicket(form);
  });
}
wireJournalForm();

// ---- boot ---------------------------------------------------------------------

loadState();
loadWatchlist().then(startLiveWatchlistLoop);
loadAlerts();
loadNarrativeAvailability();
startLiveChartLoop();
startMarketsLoop();
setInterval(() => { renderWatchlistStatus(); renderChartSummary(); renderMarketsStatus(); }, 1000); // ticks the "updated Ns ago" text between real polls
