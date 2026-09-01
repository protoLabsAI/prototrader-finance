"""The Quant Desk page — one console surface, four browsing panes.

Served on the PUBLIC ``/plugins/prototrader-finance`` prefix (plugin-view rule 1):
a browser iframe navigation carries no Authorization header, so a page behind the
bearer gate renders as a blank surface. Everything it *fetches* is gated.

**Why a view at all.** Chat already answers questions, and `show_artifact` already
renders generated visuals — so a plugin pane has to earn its place by doing the
one thing chat cannot: browsing. All four tabs here are browsing surfaces (a book,
a curve, a factor table, a ledger) and none is a question box. Anything
conversational belongs in chat with the desk subagents, not in this iframe.

Vanilla JS + inline SVG on purpose: no build step keeps the whole bundle a
drop-in Python package, and the DS plugin-kit supplies the theme tokens, the
`protoagent:init` handshake and slug-aware authed fetch.
"""

from __future__ import annotations

API = "/api/plugins/prototrader-finance"

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quant Desk</title>
<script>
  // Slug-aware base (ADR 0042, plugin-view rule 3): the iframe loads at /plugins/...
  // on the host window but /agents/<slug>/plugins/... through the fleet proxy — a
  // hardcoded absolute path there hits the HUB agent, never this one. The kit CSS
  // link is written here so its href carries the base too.
  window.__base = location.pathname.split("/plugins/")[0];
  document.write('<link rel="stylesheet" href="' + window.__base + '/_ds/plugin-kit.css">');
</script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;min-height:100%;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans);font-size:14px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1140px;margin:0 auto;padding:var(--pl-space-6) var(--pl-space-7) 64px}

  /* header */
  .top{display:flex;align-items:flex-start;gap:var(--pl-space-4);flex-wrap:wrap;margin-bottom:var(--pl-space-4)}
  .top__id{flex:1 1 320px;min-width:0}
  h1{font-size:19px;margin:0;letter-spacing:-.01em;font-weight:600}
  h1 span{color:var(--pl-color-accent)}
  .sub{color:var(--pl-color-fg-muted);font-size:12.5px;margin:3px 0 0}
  .top__act{display:flex;align-items:center;gap:var(--pl-space-2);flex-shrink:0}

  /* provenance chip — the honesty affordance */
  .prov{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;
    padding:4px 9px;border-radius:999px;white-space:nowrap;
    border:var(--pl-border-width) solid var(--pl-color-border);color:var(--pl-color-fg-muted)}
  .prov b{font-weight:600;color:var(--pl-color-fg)}
  .prov--live{border-color:color-mix(in srgb,var(--pl-color-status-success) 45%,transparent)}
  .prov--seed{border-color:color-mix(in srgb,var(--pl-color-status-warning) 45%,transparent)}
  .prov i{width:6px;height:6px;border-radius:50%;background:var(--pl-color-fg-muted);flex:none}
  .prov--live i{background:var(--pl-color-status-success)}
  .prov--seed i{background:var(--pl-color-status-warning)}
  .prov--cache i{background:var(--pl-color-status-info,var(--pl-color-accent))}

  /* layout */
  .pl-tabs{margin-bottom:var(--pl-space-5)}
  .pane{display:none}.pane--on{display:block}
  .grid{display:grid;gap:var(--pl-space-4)}
  @media(min-width:900px){.grid--2{grid-template-columns:1fr 1fr}}
  .pl-card{padding:0;overflow:hidden;margin-bottom:var(--pl-space-4)}
  .pl-panel-header{padding:var(--pl-space-3) var(--pl-space-4)}
  .body{padding:var(--pl-space-4)}
  .body--flush{padding:0}

  /* stat row */
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
    gap:1px;background:var(--pl-color-border);border-radius:var(--pl-radius);overflow:hidden;
    margin-bottom:var(--pl-space-4)}
  .stat{background:var(--pl-color-bg-raised);padding:var(--pl-space-4)}
  .stat__num{font-size:21px;font-weight:600;letter-spacing:-.02em;
    font-variant-numeric:tabular-nums;line-height:1.15}
  .stat__label{font-size:11px;color:var(--pl-color-fg-muted);margin-top:5px;
    text-transform:uppercase;letter-spacing:.06em}
  .stat__sub{font-size:11.5px;color:var(--pl-color-fg-muted);margin-top:2px}
  .pos{color:var(--pl-color-status-success)}
  .neg{color:var(--pl-color-status-error)}

  /* tables */
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--pl-color-fg-muted);
    font-weight:600;text-align:right;padding:9px var(--pl-space-4);
    border-bottom:var(--pl-border-width) solid var(--pl-color-border);white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  td{padding:9px var(--pl-space-4);text-align:right;white-space:nowrap;
    border-bottom:var(--pl-border-width) solid color-mix(in srgb,var(--pl-color-border) 55%,transparent)}
  tbody tr:last-child td{border-bottom:none}
  tbody tr:hover{background:color-mix(in srgb,var(--pl-color-fg) 3%,transparent)}
  .sym{font-weight:600}
  .muted{color:var(--pl-color-fg-muted)}
  .wrapscroll{overflow-x:auto}

  /* charts */
  svg{display:block;width:100%}
  .curve{height:280px}
  .spark{width:88px;height:22px;vertical-align:middle}
  .eqspark{width:100%;height:44px;margin-top:8px}
  .stat--wide{grid-column:span 2}
  .legend{display:flex;gap:16px;font-size:11.5px;color:var(--pl-color-fg-muted);align-items:center}
  .legend i{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}

  /* factor IC bars — centred at zero so sign reads instantly */
  .icbar{position:relative;width:120px;height:16px;margin-left:auto}
  .icbar__mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--pl-color-border)}
  .icbar__fill{position:absolute;top:3px;bottom:3px;border-radius:2px}

  form{display:flex;gap:var(--pl-space-3);flex-wrap:wrap;align-items:flex-end}
  label{display:flex;flex-direction:column;gap:5px;font-size:10.5px;color:var(--pl-color-fg-muted);
    text-transform:uppercase;letter-spacing:.06em}
  .pl-input,.pl-select{min-width:130px}
  .note{color:var(--pl-color-fg-muted);font-size:11.5px;line-height:1.6;margin:var(--pl-space-4) 0 0}
  .skel{height:13px;border-radius:4px;background:color-mix(in srgb,var(--pl-color-fg) 9%,transparent);
    animation:pulse 1.4s ease-in-out infinite}
  @keyframes pulse{50%{opacity:.45}}
</style></head><body><div class="wrap">

  <div class="top">
    <div class="top__id">
      <h1>Quant <span>Desk</span></h1>
      <p class="sub">Research surface for the finance toolset — book, backtests, factor IC, and the paper ledger.</p>
    </div>
    <div class="top__act">
      <span id="prov" class="prov"><i></i><span>loading…</span></span>
      <button id="refresh" class="pl-btn pl-btn--sm" title="Refetch from the live provider">Refresh</button>
    </div>
  </div>

  <div class="pl-tabs" role="tablist">
    <button class="pl-tab pl-tab--active" data-pane="overview" role="tab">Overview</button>
    <button class="pl-tab" data-pane="backtest" role="tab">Backtest</button>
    <button class="pl-tab" data-pane="factors" role="tab">Factors</button>
    <button class="pl-tab" data-pane="ledger" role="tab">Ledger</button>
  </div>

  <section id="pane-overview" class="pane pane--on">
    <div id="ov-stats" class="stats"></div>
    <div id="ov-demo"></div>
    <div id="ov-equity"></div>
    <div class="pl-card"><div class="pl-panel-header">
        <h2 class="pl-panel-header__title">Positions</h2>
        <span class="pl-panel-header__kicker">marked at the last available close</span></div>
      <div class="body--flush wrapscroll"><div id="ov-pos"></div></div></div>
    <div class="pl-card"><div class="pl-panel-header">
        <h2 class="pl-panel-header__title">Market</h2>
        <span class="pl-panel-header__kicker">the bundled demo universe · sorted by today's move</span></div>
      <div class="body--flush wrapscroll"><div id="ov-mkt"></div></div></div>
  </section>

  <section id="pane-backtest" class="pane">
    <div class="pl-card"><div class="body">
      <form id="bt-form">
        <label>Symbol<input id="bt-symbol" class="pl-input" list="bt-universe" autocomplete="off"></label>
        <datalist id="bt-universe"></datalist>
        <label>Strategy<select id="bt-strategy" class="pl-select"></select></label>
        <label>Period<select id="bt-period" class="pl-select">
          <option>1y</option><option selected>2y</option><option>3y</option><option>5y</option></select></label>
        <button id="bt-run" type="submit" class="pl-btn pl-btn--primary">Run backtest</button>
      </form>
    </div></div>
    <div id="bt-err" class="pl-callout pl-callout--error" hidden><div class="pl-callout__body"></div></div>
    <div id="bt-stats" class="stats"></div>
    <div class="pl-card"><div class="pl-panel-header">
        <div class="legend">
          <span><i style="background:var(--pl-color-accent)"></i>Strategy</span>
          <span><i style="background:var(--pl-color-fg-muted)"></i>Buy &amp; hold</span>
          <span id="bt-range" class="muted"></span></div></div>
      <div class="body"><svg id="bt-svg" class="curve" viewBox="0 0 800 280" preserveAspectRatio="none"></svg></div></div>
    <p class="note">Equity is growth of $1, net of 5bps cost and 2bps slippage on turnover.
      Signals are computed on data available at the time — no look-ahead. Past performance
      does not indicate future results; this is research output, not advice.</p>
  </section>

  <section id="pane-factors" class="pane">
    <div class="pl-card"><div class="pl-panel-header">
        <h2 class="pl-panel-header__title">Factor zoo</h2>
        <span class="pl-panel-header__kicker">rank correlation of each factor with forward 21-day returns</span></div>
      <div class="body--flush wrapscroll"><div id="fx-table"></div></div></div>
    <p class="note" id="fx-note"></p>
  </section>

  <section id="pane-ledger" class="pane">
    <div id="lg-gate"></div>
    <div class="grid grid--2">
      <div class="pl-card"><div class="pl-panel-header">
          <h2 class="pl-panel-header__title">Mandate</h2>
          <span class="pl-panel-header__kicker">the limits an order is checked against</span></div>
        <div class="body--flush wrapscroll"><div id="lg-mandate"></div></div></div>
      <div class="pl-card"><div class="pl-panel-header">
          <h2 class="pl-panel-header__title">Book</h2>
          <span class="pl-panel-header__kicker">open paper positions</span></div>
        <div class="body--flush wrapscroll"><div id="lg-pos"></div></div></div>
    </div>
    <div class="pl-card"><div class="pl-panel-header">
        <h2 class="pl-panel-header__title">Fill history</h2>
        <span class="pl-panel-header__kicker">append-only audit ledger, newest first</span></div>
      <div class="body--flush wrapscroll"><div id="lg-orders"></div></div></div>
  </section>
</div>

<script type="module">
// The DS plugin-kit owns the protoagent:init handshake (bearer + live re-themes onto
// the --pl-* tokens) and slug-aware authed fetch. It's an ES MODULE, so it loads via
// dynamic import — a classic <script src> throws on its exports. An older host with
// no /_ds falls back to a tokenless same-origin shim (fine locally; a gated instance
// always serves the kit).
let kit;
try { kit = await import(window.__base + "/_ds/plugin-kit.js"); }
catch (e) { kit = { initPluginView(){}, apiFetch: (p, i) => fetch(window.__base + p, i) }; }

const API = "__API__";
const $ = (id) => document.getElementById(id);
const api = (p) => kit.apiFetch(API + p).then(r => r.json());
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

const pct  = (x, d=1) => x == null ? "–" : (x * 100).toFixed(d) + "%";
const num  = (x, d=2) => x == null ? "–" : (+x).toFixed(d);
const usd  = (x) => x == null ? "–" : (x < 0 ? "-$" : "$") + Math.abs(+x).toLocaleString(undefined,
  { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const usd2 = (x) => x == null ? "–" : "$" + (+x).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sign = (x) => x == null ? "" : (+x > 0 ? "pos" : (+x < 0 ? "neg" : ""));
const signed = (x, f) => `<span class="${sign(x)}">${x != null && +x > 0 ? "+" : ""}${f(x)}</span>`;

function table(cols, rows, empty) {
  if (!rows.length) return `<div class="body"><div class="pl-empty">${esc(empty)}</div></div>`;
  return `<table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>`
       + `<tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
const loading = (el) => { el.innerHTML = `<div class="body"><div class="skel" style="width:60%"></div></div>`; };

function setProv(p) {
  const el = $("prov");
  if (!p) { el.className = "prov"; el.innerHTML = `<i></i><span>—</span>`; return; }
  el.className = "prov prov--" + (p.source || "none");
  const mixed = p.mixed ? " (mixed)" : "";
  el.innerHTML = `<i></i><span><b>${esc(p.label || p.source)}</b>${esc(mixed)}</span>`;
  el.title = p.source === "seed"
    ? "Served from the snapshot committed to the plugin repo — NOT live data. Hit Refresh to fetch live."
    : p.source === "cache" ? "Served from this agent's on-disk cache. Hit Refresh to fetch live."
    : "Fetched from the live provider.";
}

// ── sparkline ────────────────────────────────────────────────────────────────
function spark(vals, cls = "spark", W = 88, H = 22) {
  if (!vals || vals.length < 2) return "";
  const lo = Math.min(...vals), hi = Math.max(...vals), r = (hi - lo) || 1;
  const d = vals.map((v, i) =>
    (i ? "L" : "M") + (i / (vals.length - 1) * W).toFixed(1) + " " + (H - 1 - (v - lo) / r * (H - 2)).toFixed(1)
  ).join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  const stroke = up ? "var(--pl-color-status-success)" : "var(--pl-color-status-error)";
  return `<svg class="${cls}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.4" vector-effect="non-scaling-stroke"/></svg>`;
}

// ── equity curve (with a zero/parity gridline + area fill) ───────────────────
function drawCurve(svg, equity, benchmark) {
  const W = 800, H = 280, padY = 10;
  const all = equity.concat(benchmark);
  const lo = Math.min(...all), hi = Math.max(...all), r = (hi - lo) || 1;
  const n = equity.length;
  const x = (i) => (i / (n - 1)) * W;
  const y = (v) => H - padY - ((v - lo) / r) * (H - 2 * padY);
  const path = (a) => a.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  const parity = (lo <= 1 && hi >= 1)
    ? `<line x1="0" y1="${y(1).toFixed(1)}" x2="${W}" y2="${y(1).toFixed(1)}"
         stroke="var(--pl-color-border)" stroke-width="1" stroke-dasharray="3 4"/>` : "";
  svg.innerHTML = `
    <defs><linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--pl-color-accent)" stop-opacity="0.20"/>
      <stop offset="100%" stop-color="var(--pl-color-accent)" stop-opacity="0"/>
    </linearGradient></defs>
    ${parity}
    <path d="${path(equity)} L ${W} ${H} L 0 ${H} Z" fill="url(#eqfill)" stroke="none"/>
    <path d="${path(benchmark)}" fill="none" stroke="var(--pl-color-fg-muted)" stroke-width="1.4"
      opacity="0.85" vector-effect="non-scaling-stroke"/>
    <path d="${path(equity)}" fill="none" stroke="var(--pl-color-accent)" stroke-width="2"
      vector-effect="non-scaling-stroke"/>`;
}

function stats(el, cells) {
  el.innerHTML = cells.map(c =>
    `<div class="stat"><div class="stat__num ${c.cls || ""}">${c.value}</div>
     <div class="stat__label">${esc(c.label)}</div>
     ${c.sub ? `<div class="stat__sub">${esc(c.sub)}</div>` : ""}</div>`).join("");
}

// ── panes ────────────────────────────────────────────────────────────────────
let REFRESH = 0;
const q = (p) => p + (REFRESH ? (p.includes("?") ? "&" : "?") + "refresh=1" : "");

function depsBanner(d) {
  // A missing optional dependency has one known fix. Repeating a stack-trace-ish
  // string in four panels buries it; one banner with the command doesn't.
  if (!d || !d.needs_deps) return false;
  document.querySelectorAll(".pane").forEach(p => {
    if (p.id !== "pane-overview") p.innerHTML = "";
  });
  $("ov-stats").innerHTML = ""; $("ov-pos").innerHTML = ""; $("ov-mkt").innerHTML = "";
  $("ov-equity").innerHTML = "";
  $("ov-demo").innerHTML = `<div class="pl-callout pl-callout--warning"><div class="pl-callout__body">
    <b>Market-data stack not installed.</b> ${esc(d.error)}</div></div>`;
  return true;
}

async function loadOverview() {
  loading($("ov-pos")); loading($("ov-mkt"));
  const d = await api(q("/overview"));
  if (depsBanner(d)) return;
  if (!d.ok) { $("ov-pos").innerHTML = `<div class="body"><div class="pl-empty">${esc(d.error)}</div></div>`; return; }
  setProv(d.provenance);
  const p = d.portfolio;

  stats($("ov-stats"), [
    { label: "Equity", value: usd(p.equity) },
    { label: "Cash", value: usd(p.cash) },
    { label: "Invested", value: usd(p.invested) },
    { label: "Unrealized", value: signed(p.unrealized_pnl, usd), cls: "" },
    { label: "Realized", value: signed(p.realized_pnl, usd) },
    { label: "Total return", value: signed(p.total_return, pct) },
  ]);

  // sdk.record_metric gives the book a HISTORY, which a point-in-time payload
  // can't. With no host (or a fresh install) there are no points yet, so the
  // panel is omitted rather than drawn as a flat, meaningless line.
  const eq = (d.equity_history || []).map(x => x.value);
  $("ov-equity").innerHTML = eq.length > 2
    ? `<div class="pl-card"><div class="pl-panel-header">
         <h2 class="pl-panel-header__title">Equity over time</h2>
         <span class="pl-panel-header__kicker">${eq.length} recorded snapshots · sdk.record_metric</span></div>
       <div class="body">${spark(eq, "eqspark", 800, 44)}</div></div>`
    : "";

  $("ov-demo").innerHTML = p.demo
    ? `<div class="pl-callout pl-callout--info" style="margin-bottom:var(--pl-space-4)">
         <div class="pl-callout__body"><b>Sample book.</b> ${esc(p.note || "")}</div></div>` : "";

  $("ov-pos").innerHTML = table(
    ["Symbol", "Qty", "Avg", "Mark", "Value", "P&L", "%"],
    p.positions.map(r => [
      `<span class="sym">${esc(r.symbol)}</span>`, num(r.qty, 0), usd2(r.avg_price),
      usd2(r.mark) + (r.marked ? "" : ` <span class="muted" title="no fresh mark — held at cost">·</span>`),
      usd(r.value), signed(r.pnl, usd), signed(r.pnl_pct, pct),
    ]), "No open positions — the paper broker has not filled anything.");

  $("ov-mkt").innerHTML = table(
    ["Symbol", "", "Last", "1D", "1M", "6M", "1Y"],
    d.market.map(r => [
      `<span class="sym">${esc(r.symbol)}</span>`, spark(r.spark), usd2(r.last),
      signed(r.d1, pct), signed(r.m1, pct), signed(r.m6, pct), signed(r.y1, pct),
    ]), "No market data available.");
}

async function loadBacktest(ev) {
  if (ev) ev.preventDefault();
  $("bt-run").disabled = true; $("bt-err").hidden = true;
  const sym = encodeURIComponent(($("bt-symbol").value || "SPY").trim().toUpperCase());
  const d = await api(q(`/backtest?symbol=${sym}&strategy=${$("bt-strategy").value}&period=${$("bt-period").value}`))
    .catch(e => ({ ok: false, error: String(e) }));
  $("bt-run").disabled = false;

  if (depsBanner(d)) return;
  if (!d.ok) {
    $("bt-err").querySelector(".pl-callout__body").textContent = "Backtest unavailable — " + d.error;
    $("bt-err").hidden = false;
    $("bt-stats").innerHTML = ""; $("bt-svg").innerHTML = "";
    return;
  }
  setProv(d.provenance);
  const m = d.metrics, edge = m.total_return - m.bh_total_return;
  $("bt-range").textContent = `${d.symbol} · ${d.start} → ${d.end}`;
  stats($("bt-stats"), [
    { label: "CAGR", value: signed(m.cagr, pct) },
    { label: "Sharpe", value: `<span class="${sign(m.sharpe)}">${num(m.sharpe)}</span>` },
    { label: "Max drawdown", value: `<span class="neg">${pct(m.max_dd)}</span>` },
    { label: "Total return", value: signed(m.total_return, pct) },
    { label: "vs buy & hold", value: signed(edge, pct), sub: `B&H ${pct(m.bh_total_return)}` },
    { label: "Trades", value: num(m.trades, 0), sub: `${pct(m.exposure, 0)} exposure` },
  ]);
  drawCurve($("bt-svg"), d.equity, d.benchmark);
}

async function loadFactors() {
  loading($("fx-table"));
  const d = await api(q("/factors"));
  if (depsBanner(d)) return;
  if (!d.ok) { $("fx-table").innerHTML = `<div class="body"><div class="pl-empty">${esc(d.error)}</div></div>`; return; }
  setProv(d.provenance);

  const badge = { alive: "pl-badge--success", weak: "pl-badge--warning", reversed: "pl-badge--info", dead: "" };
  const peak = Math.max(0.08, ...d.factors.map(f => Math.abs(f.mean_ic || 0)));
  const bar = (ic) => {
    if (ic == null) return "";
    const w = Math.min(50, Math.abs(ic) / peak * 50);
    const c = ic >= 0 ? "var(--pl-color-status-success)" : "var(--pl-color-status-error)";
    const side = ic >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
    return `<div class="icbar"><div class="icbar__mid"></div>
      <div class="icbar__fill" style="${side};background:${c}"></div></div>`;
  };

  $("fx-table").innerHTML = table(
    ["Factor", "IC", "", "Rank IC", "IR", "Hit rate", "Rebalances", "Verdict"],
    d.factors.map(f => f.error ? [
      `<span class="sym">${esc(f.factor)}</span>`, `<span class="muted" colspan="6">${esc(f.error)}</span>`,
      "", "", "", "", "", "",
    ] : [
      `<span class="sym">${esc(f.factor)}</span><div class="muted" style="font-size:11.5px;white-space:normal;max-width:34ch">${esc(f.description || "")}</div>`,
      signed(f.mean_ic, x => num(x, 3)), bar(f.mean_ic),
      num(f.mean_rank_ic, 3), num(f.ir, 2), pct(f.hit_rate, 0), num(f.rebalances, 0),
      `<span class="pl-badge ${badge[f.verdict] || ""}">${esc(f.verdict || "–")}</span>`,
    ]), "No factor results.");

  $("fx-note").textContent =
    `IC is the cross-sectional correlation between each factor and the next 21 trading days of returns, `
  + `measured every 21 days over ${d.period} across ${d.universe.length} names `
  + `(${d.universe.join(", ")}). Factors are sign-standardised, so a positive IC means the factor `
  + `worked as intended and a strongly negative one means it reversed. A single-universe, `
  + `single-horizon study is evidence, not proof.`;
}

async function loadLedger() {
  loading($("lg-orders"));
  const d = await api(q("/ledger"));
  if (depsBanner(d)) return;
  if (d.provenance) setProv(d.provenance);
  if (!d.ok) { $("lg-orders").innerHTML = `<div class="body"><div class="pl-empty">${esc(d.error)}</div></div>`; return; }

  const g = d.gate || {};
  $("lg-gate").innerHTML =
    `<div class="pl-callout ${g.armed ? "pl-callout--warning" : "pl-callout--success"}"
       style="margin-bottom:var(--pl-space-4)"><div class="pl-callout__body">
       <b>${g.armed ? "Paper broker ARMED" : "Paper broker disarmed"}</b> — ${esc(g.reason || "")}
       ${g.armed ? "" : " Orders are refused until a mandate exists and sets <code>enabled: true</code>."}
     </div></div>`
  + (d.demo ? `<div class="pl-callout pl-callout--info" style="margin-bottom:var(--pl-space-4)">
       <div class="pl-callout__body"><b>Sample book.</b> ${esc(d.note || "")}</div></div>` : "");

  const m = d.mandate || {};
  // With no mandate file the numbers below are dataclass DEFAULTS, not limits in
  // force. Rendering them plain reads as "these limits apply", which is exactly
  // backwards — nothing applies, because nothing is armed. So they're muted and
  // labelled when the file is absent.
  const live = !!m.exists;
  const lim = (v) => live ? v : `<span class="muted">${v} <em style="font-style:normal;font-size:10.5px;opacity:.75">default</em></span>`;
  $("lg-mandate").innerHTML = m.error
    ? `<div class="body"><div class="pl-empty">${esc(m.error)}</div></div>`
    : table(["Limit", live ? "Value" : "Value (no mandate — not in force)"], [
        ["Mandate file", `<span class="muted" style="white-space:normal">${esc(m.path || "–")}</span>`],
        ["Present", live ? `<span class="pl-badge pl-badge--success">yes</span>`
                         : `<span class="pl-badge">no</span>`],
        ["Enabled", m.enabled ? `<span class="pl-badge pl-badge--warning">yes</span>`
                              : `<span class="pl-badge pl-badge--success">no</span>`],
        ["Mode", esc(m.mode || "paper")],
        ["Max order", lim(usd(m.max_order_usd))],
        ["Max position", lim(m.max_position_pct == null ? "–" : num(m.max_position_pct, 0) + "%")],
        ["Max gross exposure", lim(m.max_gross_exposure_pct == null ? "–" : num(m.max_gross_exposure_pct, 0) + "%")],
        ["Daily order cap", lim(num(m.daily_order_cap, 0))],
        ["Kill-switch", m.killswitch ? `<span class="pl-badge pl-badge--error">engaged</span>` : "clear"],
      ], "No mandate.");

  $("lg-pos").innerHTML = table(["Symbol", "Qty", "Avg", "Mark", "P&L"],
    (d.positions || []).map(r => [
      `<span class="sym">${esc(r.symbol)}</span>`, num(r.qty, 0),
      usd2(r.avg_price), usd2(r.mark), signed(r.pnl, usd),
    ]), "No open positions.");

  $("lg-orders").innerHTML = table(["When", "Symbol", "Side", "Qty", "Price", "Notional", "Fee", "Status"],
    (d.orders || []).map(o => [
      `<span class="muted">${esc((o.ts || "").slice(0, 10))}</span>`,
      `<span class="sym">${esc(o.symbol)}</span>`,
      `<span class="pl-badge ${o.side === "buy" ? "pl-badge--success" : "pl-badge--info"}">${esc(o.side)}</span>`,
      num(o.qty, 0), usd2(o.price), usd(o.notional ?? (o.qty * o.price)), usd2(o.fee),
      `<span class="muted">${esc(o.status || "filled")}</span>`,
    ]), "No fills yet — nothing has been executed against this book.");
}

// ── tabs + boot ──────────────────────────────────────────────────────────────
const LOADERS = { overview: loadOverview, backtest: loadBacktest, factors: loadFactors, ledger: loadLedger };
const loaded = new Set();

function show(name, force) {
  document.querySelectorAll(".pl-tab").forEach(b =>
    b.classList.toggle("pl-tab--active", b.dataset.pane === name));
  document.querySelectorAll(".pane").forEach(p =>
    p.classList.toggle("pane--on", p.id === "pane-" + name));
  if (force || !loaded.has(name)) { loaded.add(name); LOADERS[name](); }
}
document.querySelectorAll(".pl-tab").forEach(b =>
  b.addEventListener("click", () => show(b.dataset.pane)));

$("bt-form").addEventListener("submit", loadBacktest);
$("refresh").addEventListener("click", async () => {
  REFRESH = 1; $("refresh").disabled = true; $("refresh").classList.add("pl-btn--loading");
  const active = document.querySelector(".pl-tab--active").dataset.pane;
  loaded.clear();
  try { await LOADERS[active](); } finally {
    REFRESH = 0; $("refresh").disabled = false; $("refresh").classList.remove("pl-btn--loading");
    loaded.add(active);
  }
});

async function bootstrap() {
  const u = await api("/universe").catch(() => ({}));
  const syms = u.symbols || [];
  $("bt-universe").innerHTML = syms.map(s => `<option value="${esc(s)}">`).join("");
  $("bt-symbol").value = u.default_symbol || syms[0] || "SPY";
  const s = await api("/strategies").catch(() => ({ strategies: ["ma_cross"] }));
  $("bt-strategy").innerHTML = (s.strategies || []).map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  show("overview");
}

// Boot ONCE, on whichever fires first: the handshake (normal — and on a gated
// instance the bearer arrives with it, so data calls authenticate), or a short
// timer for the no-handshake case (standalone page / older host).
let booted = false;
const boot = () => { if (!booted) { booted = true; bootstrap(); } };
kit.initPluginView(boot);
setTimeout(boot, 800);
</script></body></html>"""


def render(config: dict | None = None) -> str:
    """The page, with the API prefix substituted.

    No benchmark is baked in: the symbol field is populated from the gated
    `/universe` response at boot. v0.3.0 also replaced a `__DEFAULT_SYMBOL__`
    token that the markup had stopped containing — a no-op that read like a
    working template hook.
    """
    return PAGE.replace("__API__", API)
