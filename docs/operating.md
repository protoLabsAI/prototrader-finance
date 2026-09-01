# Operating guide

For the person running this, not reading it. See [reference.md](./reference.md) for
shapes and arguments.

## Data freshness

Three tiers, and the dashboard always says which one answered.

| Tier | Where from | When used |
|---|---|---|
| `live` | yfinance / ccxt | Only on an explicit **Refresh**, a `?refresh=1` request, or a lifecycle warm |
| `cache` | the plugin's own store | Any normal read, when a cached frame exists |
| `seed` | `seed/` in the repo | Any normal read with no cache — a clean install |

### Why a panel says "bundled snapshot" on a machine with internet

**This is expected, not a fault.** A dashboard paint never blocks on the network —
that's deliberate, so a demo can't be broken by a captive portal or a rate limit.
Prices come from disk; the network is touched only by an explicit refresh or by the
lifecycle warm.

The warm runs at **app load** and **after the machine wakes**, refreshing any symbol
whose cache is older than 6 hours. So on a normal host you'll see `cached` shortly
after boot. Until the first warm completes, or on a host that has never reached a
provider, you'll see `bundled snapshot` — with its age, so you can judge it.

To force live data now: the **Refresh** button, top right of the view.

A panel built from many symbols reports the **weakest** tier that contributed. A
strip where 25 names are live and one came off the snapshot is not a live strip.

### Refreshing the bundled snapshot

```bash
python scripts/refresh_seed.py               # the whole demo universe
python scripts/refresh_seed.py SPY NVDA      # just these
```

Real bars, committed deliberately so a clean install demos offline. A partial run
merges into the manifest rather than replacing it.

## Arming the paper broker

Nothing trades until you do this, on purpose. Even armed, it is paper only —
`mode: live` is not implemented and refuses.

1. **Find the path.** Open the **Ledger** tab; the Mandate card shows exactly where
   the file is expected. (Or set `broker_mandate_path` in Settings to put it
   elsewhere — a read-only mount, or beside your other config.)
2. **Copy the example.** `broker/broker_mandate.example.yaml` → that path, named
   `broker_mandate.yaml`.
3. **Set the limits, then arm.** Edit `enabled: true` last, once the limits read the
   way you want:

   | Field | Meaning |
   |---|---|
   | `starting_cash` | Opening paper cash. First run only — state persists after |
   | `universe` | Symbol allowlist. Empty = any symbol, still bounded by the limits |
   | `max_order_usd` | Max notional on a single order |
   | `max_position_pct` | Max % of equity in one symbol |
   | `max_gross_exposure_pct` | Max total invested as % of equity (100 = no leverage) |
   | `daily_order_cap` | Max orders per calendar day |
   | `require_approval` | Per-order human approval. Keep it true |

4. **Check the Ledger.** The banner flips to "Paper broker ARMED". Until a mandate
   file exists, the limit values shown there are dataclass defaults and are labelled
   *not in force* — nothing is armed, so nothing applies.

Every order then runs: mandate → kill-switch → limit checks → **human approval**
(the turn pauses until you type APPROVE) → simulated fill → append-only audit ledger.

## The kill-switch

Create a file named `TRADING_HALT` and every order is refused immediately.

It is honoured in **both** the plugin's own store and the host's config dir. Someone
reaching for a kill-switch is having a bad day, and a halt file in the "wrong" one of
those must still stop trading. Remove the file to resume.

The Ledger's Mandate card shows whether it's engaged, and `broker_account` reports
the same state — both read every location the gate reads.

## Troubleshooting

**Every panel says "Market-data stack not installed".**
`requires_pip` is declared, not auto-installed. Run:
`python -m server plugin install-deps prototrader-finance`, then restart.
pandas and numpy are required; yfinance and ccxt are optional — without them
everything still renders from the bundled snapshot, you just can't fetch live.

**The rail icon is there but every panel fails to fetch.**
Same cause as above in almost every case. If the deps are installed, check the
server log at plugin load: each contribution group reports separately, so a failed
router is named.

**The dashboard shows a sample book I didn't create.**
With no fills yet, the Ledger falls back to a bundled sample so the tab isn't empty
in a demo. It's labelled "Sample book" wherever it appears. Place a paper order and
your real book replaces it. Goal verifiers refuse to grade the sample.

**A goal on portfolio return says "no real paper book yet".**
By design — see above. Arm a mandate and place an order first.

**A drawdown watch never fires.**
The high-water mark comes from the recorded equity series, which needs history.
Equity is recorded on each fill and each lifecycle warm; a fresh install has none.

**Test connection fails.**
It reports which tier is reachable. "Live provider unreachable" is still a *pass* —
the plugin genuinely works from the snapshot. It's a failure only when no tier has
data at all.

## Where state lives

Everything the plugin owns is in its instance-scoped store (`sdk.plugin_store`), so
the dev sandbox and each fleet member keep separate books:

| File | What |
|---|---|
| `broker_paper.json` | Positions and cash |
| `broker_audit.jsonl` | Append-only fill ledger |
| `broker_mandate.yaml` | The mandate, unless `broker_mandate_path` points elsewhere |
| `TRADING_HALT` | The kill-switch, when present |
| `cache/*.csv.gz` | Price cache — rebuildable, safe to delete |

The Ledger tab shows the resolved path. v0.1.0 wrote some of these into the host's
config directory; they are migrated on first load of a newer version.
