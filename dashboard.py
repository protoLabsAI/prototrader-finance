"""Quant Desk dashboard — the plugin's console view (ADR 0026).

A self-contained page served at ``/plugins/prototrader-finance/dashboard`` and a
JSON API (``/api/backtest``, ``/api/strategies``) it calls. The console renders a
left-rail icon (manifest ``views:``) whose panel is an iframe of this page; on
load the console ``postMessage``s a bearer token + theme tokens, which the page
applies (the ADR 0026 handshake). No build step — vanilla JS + inline SVG, so the
whole bundle stays a drop-in Python package.

The API drives the real backtest engine (``backtest.engine``): fetch → signal →
simulate → metrics, returning the strategy vs buy-and-hold equity curves. Network
failures (offline / unknown symbol) degrade to a clear error the page renders —
never a 500.
"""

from __future__ import annotations

_STRATEGIES = ["ma_cross", "rsi_meanrev", "breakout", "buy_hold"]


def build_dashboard_router(config: dict | None):
    """A FastAPI router for the dashboard page + its backtest API. Closes over the
    plugin config (ADR 0019) so the page defaults to the configured benchmark."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse, JSONResponse

    router = APIRouter()
    default_symbol = ((config or {}).get("default_benchmark") or "SPY").upper()

    @router.get("/dashboard")
    async def _dashboard():  # the iframe page (manifest views[].path)
        return HTMLResponse(_PAGE.replace("__DEFAULT_SYMBOL__", default_symbol))

    @router.get("/api/strategies")
    async def _strategies():
        return JSONResponse({"strategies": _STRATEGIES})

    @router.get("/api/backtest")
    async def _backtest(symbol: str = default_symbol, strategy: str = "ma_cross", period: str = "2y"):
        """Run the backtest engine and return both equity curves + headline metrics.

        Errors degrade to ``{ok: false, error}`` (HTTP 200) so the page can show a
        clean message instead of a broken panel."""
        if strategy not in _STRATEGIES:
            return JSONResponse({"ok": False, "error": f"unknown strategy {strategy!r}"})
        try:
            from .backtest import engine

            df = engine.fetch_ohlcv(symbol, period=period)
            pos = engine.signals(df, strategy, {})
            sim = engine.simulate(df, pos)
            m = engine.metrics(sim, df.index)
            keys = ("cagr", "sharpe", "max_dd", "total_return", "bh_total_return", "trades", "exposure")
            return JSONResponse(
                {
                    "ok": True,
                    "symbol": symbol.upper(),
                    "strategy": strategy,
                    "start": str(df.index[0].date()),
                    "end": str(df.index[-1].date()),
                    "dates": [str(d.date()) for d in df.index],
                    "equity": [round(float(x), 4) for x in sim["equity"]],
                    "benchmark": [round(float(x), 4) for x in sim["bh_equity"]],
                    "metrics": {k: (round(float(m[k]), 4) if isinstance(m.get(k), (int, float)) else m.get(k)) for k in keys},
                }
            )
        except Exception as e:  # offline / bad symbol / missing deps → readable message
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})

    return router


# ── the page ─────────────────────────────────────────────────────────────────
# Vanilla JS + inline SVG. Dark-first; applies the console's --pl-* theme tokens
# when the ADR 0026 handshake delivers them, and uses the handed-in bearer token
# (if any) for its same-origin API calls.
_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quant Desk</title>
<script>
  // Slug-aware base (protoAgent ADR 0042, plugin-view rule 3): the iframe loads at
  // /plugins/... on the host window but /agents/<slug>/plugins/... through the fleet
  // proxy — hardcoded absolute paths there hit the HUB agent, never this one. The kit
  // CSS link is injected so its href carries the base too.
  window.__base = location.pathname.split("/plugins/")[0];
  document.write('<link rel="stylesheet" href="' + window.__base + '/_ds/plugin-kit.css">');
</script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans);font-size:14px}
  .wrap{max-width:880px;margin:0 auto;padding:var(--pl-space-6) var(--pl-space-8)}
  h1{font-size:18px;margin:0 0 2px;color:var(--pl-color-accent);letter-spacing:.2px}
  .sub{color:var(--pl-color-fg-muted);font-size:12.5px;margin:0 0 18px}
  form{display:flex;gap:var(--pl-space-3);flex-wrap:wrap;align-items:flex-end;margin-bottom:18px}
  label{display:flex;flex-direction:column;gap:4px;font-size:11px;color:var(--pl-color-fg-muted);text-transform:uppercase;letter-spacing:.05em}
  form .pl-input,form .pl-select{min-width:120px}
  .chart{background:var(--pl-color-bg-raised);border:var(--pl-border-width) solid var(--pl-color-border);border-radius:var(--pl-radius);margin-bottom:16px}
  .chart__body{padding:var(--pl-space-3)}
  .legend{display:flex;gap:18px;font-size:12px;margin:0 0 8px 4px}
  .legend i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
  svg{width:100%;height:260px;display:block}
  #metrics .pl-stat__num.pos{color:var(--pl-color-status-success)}
  #metrics .pl-stat__num.neg{color:var(--pl-color-status-error)}
  .note{color:var(--pl-color-fg-muted);font-size:11.5px;margin-top:14px;line-height:1.5}
</style></head><body><div class="wrap">
  <h1>Quant Desk</h1>
  <p class="sub">Vectorized backtest with realistic costs vs buy-and-hold — research, not advice.</p>
  <form id="f">
    <label>Symbol<input id="symbol" class="pl-input" value="__DEFAULT_SYMBOL__" autocomplete="off"></label>
    <label>Strategy<select id="strategy" class="pl-select"></select></label>
    <label>Period<select id="period" class="pl-select">
      <option>1y</option><option selected>2y</option><option>5y</option></select></label>
    <button id="run" type="submit" class="pl-btn pl-btn--primary">Backtest</button>
  </form>
  <div id="err" class="pl-callout pl-callout--error" hidden></div>
  <div class="chart">
    <div class="pl-panel-header">
      <div class="legend"><span><i style="background:var(--pl-color-accent)"></i>Strategy</span>
        <span><i style="background:var(--pl-color-fg-muted)"></i>Buy &amp; hold</span>
        <span id="range" style="color:var(--pl-color-fg-muted)"></span></div>
    </div>
    <div class="chart__body">
      <svg id="svg" viewBox="0 0 800 260" preserveAspectRatio="none"></svg>
    </div>
  </div>
  <div class="pl-stats" id="metrics"></div>
  <p class="note">Equity is growth of $1, net of 5bps cost + 2bps slippage on turnover.
    Past performance is not indicative of future results. The paper broker (a separate
    tool) stays OFF until a mandate exists.</p>
</div>
<script>
// ── ADR 0038 handshake: receive the bearer token + curated theme from the console,
// bridging the curated {bg,bgPanel,…} keys onto the DS --pl-* tokens.
const TMAP={bg:["--pl-color-bg"],bgPanel:["--pl-color-bg-raised","--pl-color-bg-subtle"],fg:["--pl-color-fg"],fgMuted:["--pl-color-fg-muted"],brand:["--pl-color-accent"],border:["--pl-color-border"]};
let TOKEN=null;
function applyTheme(t){const r=document.documentElement;for(const[k,v] of Object.entries(t||{}))(TMAP[k]||(k.startsWith("--pl-")?[k]:[])).forEach(p=>v&&r.style.setProperty(p,v));}
window.addEventListener("message",(e)=>{const d=e.data||{};
  if(d.type==="protoagent:init"){if(d.token)TOKEN=d.token;applyTheme(d.theme);}
  else if(d.type==="protoagent:theme")applyTheme(d.theme);});

const api = (p) => fetch(window.__base + p, TOKEN ? {headers: {Authorization: "Bearer " + TOKEN}} : {}).then(r => r.json());
const $ = (id) => document.getElementById(id);
const fmtPct = (x) => (x == null ? "–" : (x * 100).toFixed(1) + "%");
const fmtNum = (x) => (x == null ? "–" : (+x).toFixed(2));

async function loadStrategies() {
  const r = await api("/plugins/prototrader-finance/api/strategies").catch(() => ({strategies: ["ma_cross"]}));
  $("strategy").innerHTML = r.strategies.map(s => `<option value="${s}">${s}</option>`).join("");
}

function drawCurve(dates, equity, benchmark) {
  const W = 800, H = 260, pad = 6;
  const all = equity.concat(benchmark);
  const lo = Math.min(...all), hi = Math.max(...all);
  const n = equity.length;
  const x = (i) => pad + (i / (n - 1)) * (W - 2 * pad);
  const y = (v) => H - pad - ((v - lo) / (hi - lo || 1)) * (H - 2 * pad);
  const path = (arr) => arr.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  $("svg").innerHTML =
    `<path d="${path(benchmark)}" fill="none" stroke="var(--pl-color-fg-muted)" stroke-width="1.5" opacity="0.8"/>` +
    `<path d="${path(equity)}" fill="none" stroke="var(--pl-color-accent)" stroke-width="2"/>`;
}

function showMetrics(m) {
  const cell = (k, label, v, signed) => {
    const cls = signed ? (v >= 0 ? "pos" : "neg") : "";
    return `<div><div class="pl-stat__num ${cls}">${v}</div><div class="pl-stat__label">${label}</div></div>`;
  };
  $("metrics").innerHTML =
    cell("cagr", "CAGR", fmtPct(m.cagr), true) +
    cell("sharpe", "Sharpe", fmtNum(m.sharpe), true) +
    cell("max_dd", "Max DD", fmtPct(m.max_dd), true) +
    cell("total_return", "Total", fmtPct(m.total_return), true) +
    cell("bh", "vs B&H", fmtPct(m.total_return - m.bh_total_return), true) +
    cell("trades", "Trades", m.trades) +
    cell("exposure", "Exposure", fmtPct(m.exposure));
}

async function run(ev) {
  if (ev) ev.preventDefault();
  $("run").disabled = true; $("err").hidden = true;
  const sym = encodeURIComponent($("symbol").value.trim() || "SPY");
  const strat = $("strategy").value, per = $("period").value;
  try {
    const r = await api(`/plugins/prototrader-finance/api/backtest?symbol=${sym}&strategy=${strat}&period=${per}`);
    if (!r.ok) { $("err").textContent = "Backtest unavailable: " + r.error; $("err").hidden = false; }
    else {
      $("range").textContent = `${r.start} → ${r.end} · ${r.symbol}`;
      drawCurve(r.dates, r.equity, r.benchmark);
      showMetrics(r.metrics);
    }
  } catch (e) { $("err").textContent = "Request failed: " + e; $("err").hidden = false; }
  $("run").disabled = false;
}

$("f").addEventListener("submit", run);
loadStrategies().then(run);
</script></body></html>"""
