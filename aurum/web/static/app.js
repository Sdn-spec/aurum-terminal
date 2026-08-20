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

// ---- watchlist ----------------------------------------------------------

async function loadWatchlist() {
  const status = document.getElementById("watchlist-status");
  const body = document.getElementById("watchlist-body");
  const watchlist = await getJSON("/api/watchlist");
  body.innerHTML = watchlist
    .map(
      (w, i) =>
        `<tr class="watch-row" data-name="${w.name}" style="animation-delay:${i * 45}ms"><td><div class="sym-cell">${iconFor(w.name)}<span>${w.name}</span></div></td><td>${w.ticker}</td>` +
        `<td class="c-num" data-cell="last">—</td><td class="c-num" data-cell="high">—</td><td class="c-num" data-cell="low">—</td></tr>`
    )
    .join("");
  body.querySelectorAll("tr").forEach((row) => row.addEventListener("click", () => selectSymbol(row.dataset.name)));

  let errors = 0;
  for (let i = 0; i < watchlist.length; i++) {
    if (i > 0) await sleep(WATCHLIST_ROW_DELAY_MS);
    const { name } = watchlist[i];
    status.innerHTML = `<span class="spinner"></span>Fetching quotes… (${i + 1}/${watchlist.length})`;
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
  status.textContent = errors ? `Done with ${errors} error(s) — Yahoo may be rate-limiting; try Refresh again shortly.` : `Quotes updated ${new Date().toLocaleTimeString()}`;
  status.classList.toggle("error", errors > 0);

  if (!selectedSymbol && watchlist.length) selectSymbol(watchlist[0].name);
}
document.getElementById("refresh-quotes").addEventListener("click", loadWatchlist);

function selectSymbol(name) {
  selectedSymbol = name;
  document.querySelectorAll("#watchlist-body tr").forEach((r) => r.classList.toggle("selected", r.dataset.name === name));
  document.getElementById("chart-title").innerHTML = `${iconFor(name)} Chart — ${name}`;
  loadChart(name);
}

// ---- chart (shared SVG line-chart renderer, with glow + draw-in animation) --

function renderLineChart(svgEl, series, { showLegend = false } = {}) {
  // series: [{ label, color, points: [{x: unixSeconds, y: number}] }]
  const W = 640, H = 220, padL = 56, padR = 14, padT = 16, padB = 16;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const allPoints = series.flatMap((s) => s.points);
  if (!allPoints.length) { svgEl.innerHTML = ""; return; }
  const minX = Math.min(...allPoints.map((p) => p.x)), maxX = Math.max(...allPoints.map((p) => p.x));
  let minY = Math.min(...allPoints.map((p) => p.y)), maxY = Math.max(...allPoints.map((p) => p.y));
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const rangeY0 = maxY - minY;
  minY -= rangeY0 * 0.08; maxY += rangeY0 * 0.08;
  const rangeY = maxY - minY, rangeX = maxX - minX || 1;

  const xAt = (x) => padL + ((x - minX) / rangeX) * plotW;
  const yAt = (y) => padT + plotH - ((y - minY) / rangeY) * plotH;

  const gridSteps = [minY + rangeY * 0.15, minY + rangeY * 0.5, minY + rangeY * 0.85];
  let svg = `<defs>
    <linearGradient id="chartFade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
    </linearGradient>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="3.2" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>`;
  svg += gridSteps
    .map((v) => {
      const y = yAt(v).toFixed(2);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="var(--border)" stroke-width="1" />` +
        `<text x="${padL - 8}" y="${y}" text-anchor="end" dominant-baseline="middle" font-family="var(--font-mono)" font-size="10" fill="var(--text-muted)">${fmtPlain(v)}</text>`;
    })
    .join("");

  series.forEach((s, si) => {
    if (!s.points.length) return;
    const path = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${xAt(p.x).toFixed(2)},${yAt(p.y).toFixed(2)}`).join(" ");
    const last = s.points[s.points.length - 1];
    if (si === 0) {
      const baseline = padT + plotH;
      const areaPath = path + ` L${xAt(last.x).toFixed(2)},${baseline} L${xAt(s.points[0].x).toFixed(2)},${baseline} Z`;
      svg += `<path class="chart-area" d="${areaPath}" fill="url(#chartFade)" stroke="none" />`;
    }
    svg += `<path class="chart-line" data-series="${si}" d="${path}" fill="none" stroke="${s.color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" ${si === 0 ? 'filter="url(#glow)"' : ""} />`;
    svg += `<circle class="chart-endpoint" cx="${xAt(last.x).toFixed(2)}" cy="${yAt(last.y).toFixed(2)}" r="5" fill="${s.color}" stroke="var(--surface-solid)" stroke-width="2" filter="url(#glow)" />`;
  });

  if (showLegend) {
    let lx = padL;
    series.forEach((s) => {
      svg += `<rect x="${lx}" y="${H - 6}" width="10" height="3" fill="${s.color}" /><text x="${lx + 14}" y="${H - 3}" font-family="var(--font-mono)" font-size="10" fill="var(--text-secondary)">${s.label}</text>`;
      lx += 14 + s.label.length * 6 + 16;
    });
  }
  svgEl.innerHTML = svg;

  // set each drawn line's dash length to its own real path length so the draw-in
  // animation traces the actual curve instead of a guessed constant.
  svgEl.querySelectorAll(".chart-line").forEach((line) => {
    const len = line.getTotalLength();
    line.style.setProperty("--line-len", len);
  });
}

// ---- candlestick renderer ---------------------------------------------------

function renderCandlestickChart(svgEl, bars) {
  const W = 640, H = 220, padL = 56, padR = 14, padT = 16, padB = 16;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  if (!bars.length) { svgEl.innerHTML = ""; return; }

  let minY = Math.min(...bars.map((b) => b.low)), maxY = Math.max(...bars.map((b) => b.high));
  const rangeY0 = maxY - minY || 1;
  minY -= rangeY0 * 0.06; maxY += rangeY0 * 0.06;
  const rangeY = maxY - minY;

  const n = bars.length;
  const xAt = (i) => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yAt = (y) => padT + plotH - ((y - minY) / rangeY) * plotH;
  const slotW = plotW / n;
  const bodyW = Math.max(1.5, Math.min(9, slotW * 0.62));

  const gridSteps = [minY + rangeY * 0.15, minY + rangeY * 0.5, minY + rangeY * 0.85];
  let svg = gridSteps
    .map((v) => {
      const y = yAt(v).toFixed(2);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="var(--border)" stroke-width="1" />` +
        `<text x="${padL - 8}" y="${y}" text-anchor="end" dominant-baseline="middle" font-family="var(--font-mono)" font-size="10" fill="var(--text-muted)">${fmtPlain(v)}</text>`;
    })
    .join("");

  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const cls = up ? "up" : "down";
    const x = xAt(i);
    const yHigh = yAt(b.high), yLow = yAt(b.low);
    const yOpen = yAt(b.open), yClose = yAt(b.close);
    const bodyTop = Math.min(yOpen, yClose), bodyH = Math.max(1, Math.abs(yClose - yOpen));
    const delay = Math.min(i * 3, 500);
    svg += `<g class="candle-group" style="animation-delay:${delay}ms">` +
      `<line class="candle-wick ${cls}" x1="${x.toFixed(2)}" y1="${yHigh.toFixed(2)}" x2="${x.toFixed(2)}" y2="${yLow.toFixed(2)}" />` +
      `<rect class="candle-body candle-${cls}" x="${(x - bodyW / 2).toFixed(2)}" y="${bodyTop.toFixed(2)}" width="${bodyW.toFixed(2)}" height="${bodyH.toFixed(2)}" rx="0.6" />` +
      `</g>`;
  });

  svgEl.innerHTML = svg;
}

// ---- chart state + loading ---------------------------------------------------

const TIMEFRAMES = {
  "1m": { range: "7d", interval: "1m" },
  "15m": { range: "60d", interval: "15m" },
  "1d": { range: "10y", interval: "1d" },
};
const CHART_DISPLAY_BARS = 150;
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
  const recent = currentChartBars.slice(-CHART_DISPLAY_BARS);
  if (chartType === "candles") {
    renderCandlestickChart(document.getElementById("chart-svg"), recent);
  } else {
    renderLineChart(document.getElementById("chart-svg"), [
      { label: currentChartName, color: "var(--accent)", points: recent.map((b) => ({ x: b.timestamp, y: b.close })) },
    ]);
  }
}

async function loadChart(name) {
  currentChartName = name;
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
    summary.textContent = `${bars.length} ${chartTimeframe} bars cached · last ${recent.length} shown · ${fmtPct(change)} over that window`;
  } catch (err) {
    currentChartBars = [];
    summary.textContent = `Could not load history: ${err.message}`;
    summary.classList.add("error");
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

// ---- backtest -----------------------------------------------------------------

document.getElementById("run-backtest").addEventListener("click", async () => {
  const out = document.getElementById("backtest-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.innerHTML = loadingHtml("Running backtest on real history…");
  try {
    const r = await getJSON(`/api/backtest/${selectedSymbol}`);
    renderLineChart(
      document.getElementById("backtest-svg"),
      [
        { label: "Strategy", color: "var(--accent)", points: r.strategy_equity_curve.map((p) => ({ x: p.timestamp, y: p.equity })) },
        { label: "Buy & hold", color: "var(--text-muted)", points: r.buy_hold_equity_curve.map((p) => ({ x: p.timestamp, y: p.equity })) },
      ],
      { showLegend: true }
    );
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
loadWatchlist();
requestAnimationFrame(() => positionTabUnderline(document.querySelector("#tabs button.active")));
