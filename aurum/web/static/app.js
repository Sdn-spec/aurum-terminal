"use strict";

const WATCHLIST_ROW_DELAY_MS = 700; // spaced out to avoid tripping Yahoo's burst rate limit

let selectedSymbol = null;

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

// ---- tabs -------------------------------------------------------------

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + btn.dataset.tab));
});

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
      (w) =>
        `<tr data-name="${w.name}"><td>${w.name}</td><td>${w.ticker}</td>` +
        `<td class="c-num" data-cell="last">—</td><td class="c-num" data-cell="high">—</td><td class="c-num" data-cell="low">—</td></tr>`
    )
    .join("");
  body.querySelectorAll("tr").forEach((row) => row.addEventListener("click", () => selectSymbol(row.dataset.name)));

  let errors = 0;
  for (let i = 0; i < watchlist.length; i++) {
    if (i > 0) await sleep(WATCHLIST_ROW_DELAY_MS);
    const { name } = watchlist[i];
    status.textContent = `Fetching quotes… (${i + 1}/${watchlist.length})`;
    status.classList.remove("error");
    try {
      const quote = await getJSON(`/api/quote/${name}`);
      const row = body.querySelector(`tr[data-name="${name}"]`);
      row.querySelector('[data-cell="last"]').textContent = fmtPlain(quote.price);
      row.querySelector('[data-cell="high"]').textContent = fmtPlain(quote.day_high);
      row.querySelector('[data-cell="low"]').textContent = fmtPlain(quote.day_low);
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
  document.getElementById("chart-title").textContent = `Chart — ${name}`;
  loadChart(name);
}

// ---- chart (shared SVG line-chart renderer) ------------------------------

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
  let svg = gridSteps
    .map((v) => {
      const y = yAt(v).toFixed(2);
      return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="var(--border)" stroke-width="1" />` +
        `<text x="${padL - 8}" y="${y}" text-anchor="end" dominant-baseline="middle" font-family="var(--font-mono)" font-size="10" fill="var(--text-muted)">${fmtPlain(v)}</text>`;
    })
    .join("");

  series.forEach((s) => {
    if (!s.points.length) return;
    const path = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${xAt(p.x).toFixed(2)},${yAt(p.y).toFixed(2)}`).join(" ");
    svg += `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />`;
    const last = s.points[s.points.length - 1];
    svg += `<circle cx="${xAt(last.x).toFixed(2)}" cy="${yAt(last.y).toFixed(2)}" r="4.5" fill="${s.color}" stroke="var(--surface)" stroke-width="2" />`;
  });

  if (showLegend) {
    let lx = padL;
    series.forEach((s) => {
      svg += `<rect x="${lx}" y="${H - 6}" width="10" height="3" fill="${s.color}" /><text x="${lx + 14}" y="${H - 3}" font-family="var(--font-mono)" font-size="10" fill="var(--text-secondary)">${s.label}</text>`;
      lx += 14 + s.label.length * 6 + 16;
    });
  }
  svgEl.innerHTML = svg;
}

async function loadChart(name) {
  const summary = document.getElementById("chart-summary");
  summary.textContent = "Loading…";
  summary.classList.remove("error");
  try {
    const bars = await getJSON(`/api/history/${name}?range=10y&interval=1d`);
    const recent = bars.slice(-180);
    renderLineChart(document.getElementById("chart-svg"), [
      { label: name, color: "var(--accent)", points: recent.map((b) => ({ x: b.timestamp, y: b.close })) },
    ]);
    const change = recent.length > 1 ? ((recent[recent.length - 1].close - recent[0].close) / recent[0].close) * 100 : 0;
    summary.textContent = `${bars.length} daily bars cached · last ${recent.length} shown · ${fmtPct(change)} over that window`;
  } catch (err) {
    summary.textContent = `Could not load history: ${err.message}`;
    summary.classList.add("error");
  }
}

// ---- setup scanner --------------------------------------------------------

document.getElementById("run-scanner").addEventListener("click", async () => {
  const out = document.getElementById("scanner-output");
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.textContent = "Scanning…";
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
  if (!selectedSymbol) { out.textContent = "Select a symbol in the watchlist first."; return; }
  out.textContent = "Building memo…";
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
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---- optimizer -------------------------------------------------------------

document.getElementById("run-optimizer").addEventListener("click", async () => {
  const out = document.getElementById("optimizer-output");
  const method = document.getElementById("optimizer-method").value;
  out.textContent = "Loading watchlist history and optimizing… (first run can take a few seconds)";
  try {
    const r = await getJSON(`/api/optimize?method=${method}`);
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
  out.textContent = "Forecasting…";
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
  out.textContent = "Loading Kronos-mini (first run downloads ~30MB of weights)…";
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
  out.textContent = "Running backtest on real history…";
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
    out.textContent = [
      `Data: ${r.bar_count} real daily bars`,
      "",
      `Starting equity   ${fmtMoney(s.starting_equity)}`,
      `Ending equity     ${fmtMoney(s.ending_equity)}`,
      `Total return      ${fmtPct(s.total_return_pct)}`,
      `Trades            ${s.total_trades}`,
      `Win rate          ${s.win_rate_pct.toFixed(1)}%`,
      `Avg win / loss    ${fmtMoney(s.avg_win)} / ${fmtMoney(s.avg_loss)}`,
      `Profit factor     ${s.profit_factor === Infinity ? "inf" : s.profit_factor.toFixed(2)}`,
      `Max drawdown      ${s.max_drawdown_pct.toFixed(2)}%`,
    ].join("\n");
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- boot ---------------------------------------------------------------------

loadState();
loadWatchlist();
