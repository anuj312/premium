# Pulse Premium

Version: `premium-2.4-session-pressure`.

A clean NSE research terminal with real-data-only watchlists, experimental
intraday booster candidates, liquidity leaders, pre-breakout building watchlists
and cumulative session-pressure observations.
No order placement is implemented. No trading accuracy or returns are promised.

## Start

Requires Python 3.11 or newer and a valid Zerodha Kite API subscription/token
with the relevant historical-data access. From this extracted project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
export KITE_API_KEY="your_key"
export KITE_ACCESS_TOKEN="your_daily_access_token"
python3 live_scanner_server.py
```

On Windows, activate with `.venv\Scripts\activate` and set the environment
variables using your shell's normal syntax.

Open `http://127.0.0.1:8050/`. Opening the HTML directly does not connect a feed.
The existing `numbers.txt` allowlist from your upload is retained. Sign in with
an approved number. Never publish that file or commit it to a public repository.
Git intentionally ignores it; Render requires the private Secret File setup below.
The gate is an allowlist, NOT OTP verification or proof of phone ownership.
Sessions are held in memory, one per approved number, for one application worker.

`.env.example` documents options; it is not automatically loaded. Export its
values or configure them as private hosting environment variables. Do not put
real Kite credentials into source files or browser JavaScript.

## Interface

- Graphite/gold dark terminal and an optional light theme; mobile responsive.
- Momentum leaders, Booster Candidates, Sector Flow, Relative Volume, % Change.
- Liquidity directly below % Change: up to five bullish and five bearish stocks.
- Setup Building directly below Liquidity: up to five bullish and five bearish
  pre-breakout stocks, ranked by checks passed, then boundary proximity and symbol.
- Session Buy / Sell Pressure directly below Setup Building: up to five stocks
  per side, ranked by cumulative pressure from 09:15 through completed candles.
  Current resting depth and latest 5m pressure are shown separately.
- All panels respect direction, volatility and search. Sector flow is a server
  aggregate of the selected universe/sector, independent of those local filters.
- Market context always uses the configured NIFTY 50 membership, independent
  of the UI selection. Constituent definitions are supplied by the server.
- Custom baskets are equal-weight statistics, not official index prices.
  They do not have executable LTPs or liquidity/booster/building/imbalance candidates.

## Live Correctness

- Keeps exchange timestamps separately from receipt timestamps. Converts Kite
  SDK host-local naive timestamps correctly to IST, including on UTC servers.
- Rejects stale, future and out-of-order ticks and decreasing same-day volume.
- Forms full-session provisional five-minute candles from volume differences.
  Interrupted/partial bars are withheld until a complete broker backfill exists.
- Historical seeding and reconciliation store completed bars only. Completed
  broker candles override provisional sampled-quote bars deterministically.
- RSI(14) and ADX(14) use Wilder seeding/smoothing. EMA(9/21), RSI and ADX use
  the same completed sequence, with a 50-bar minimum warm-up. Five-minute and
  15-minute trend features are based on completed bars.
- General stock VWAP uses the current-session broker average traded price when
  available, otherwise a completed-bar typical-price approximation. It never substitutes
  yesterday's VWAP for today's live context. Pressure uses its separate closed-bar
  HLC3 estimate, described below.
- Intraday RVOL compares cumulative COMPLETED slots with identical historical
  clock-time slots. Zero-volume slots are preserved; incomplete sessions are
  excluded. The numerator therefore stops at the displayed volume cutoff, not
  at the current forming bar. Ten valid prior sessions are the default minimum.
- Uses up to 20 baseline sessions, seeded from 45 calendar days by default.
  Holidays/missing history can leave fewer sessions; missing values remain null.
- Regular % Change uses the close preceding the quote's actual session, with
  the broker previous-close field as fallback, never today's open. Regular
  indicators use prior completed daily candles, not a fabricated forming bar.
- NIFTY context needs 80% quote AND feature coverage and a fresh official index.
  Bullish/bearish/neutral, insufficient-data and stale states are explicit.
  Agreement is a heuristic vote fraction, not a success probability.
- Backend cache refresh and frontend polling default to three seconds. Closed-bar
  features are cached separately from live price/depth; backfill/new bars invalidate
  that cache. This is not subsecond or trade-by-trade market infrastructure.
- Booster/Building candidates expire on stale stock quotes, stale market context,
  stale API cache, session change or their own expiry. Pressure follows its separate
  freshness rules below. Browser requests have timeouts and
  out-of-order response protection. Failed requests immediately remove live status.
- No synthetic/demo data fallback is present. Previous real snapshots can remain
  visible as a clearly non-actionable watchlist.

## Liquidity Definition

Bullish/bearish here means positive/negative selected-mode price change, NOT
executed buying/selling volume. Liquidity is different from momentum.

Day traded value = broker average traded price x cumulative day volume.
Spread is measured in basis points using best bid/ask. Depth is the resting
notional value of up to five valid levels on each side.

Default eligibility: fresh stock quote, at least Rs 5 crore day traded value,
spread at most 10 bps, and at least Rs 1 lakh resting depth on EACH side.
Missing average price/depth cannot qualify. Adjust thresholds to your order size.

The heuristic score weights logarithmic traded value 60%, spread 25%, and
logarithmic smaller-side depth 15%. Rankings sort by score, then traded value.
Displayed total depth is both sides combined; eligibility checks the smaller side.
Depth can disappear through cancellations. The score does not guarantee a fill.

## Setup Building Watchlist

This is a pre-confirmation research watchlist, NOT a trade entry or accumulation
detector. It uses the completed 09:15-09:30 IST opening range and runs from 09:30
until strictly before 14:45. It is Intraday / individual stocks only.

Hard requirements:

- Fresh current-session stock quote, live API cache and current, sufficiently
  covered bullish/bearish/neutral market context. Missing context pauses the list.
- Ready indicators and EVERY completed current-session 5m slot through the present
  clock cutoff; missing, interrupted, stale, partial or future bars cannot qualify.
- Existing liquidity eligibility, direction-aligned VWAP and 5m EMA(9/21) trend.
- RVOL >= 1.0 with at least ten valid baseline sessions, by default.
- Price stays inside the opening range, within 0.5% of its same-side boundary.
  Exact touches are allowed, but price beyond the boundary is not pre-breakout.
- No completed post-opening-range candle previously closed beyond that same-side
  boundary. Re-entered, already-confirmed breakouts are not fresh building setups.

`BUILDING_MAX_GAP_PCT` and `BUILDING_MIN_RVOL` configure the last two numeric
thresholds. Bullish/bearish side follows the stock's change from today's open.
Gap = directional distance to the opening-range boundary / boundary price x 100.

Each card shows price, boundary, gap, RVOL and seven Pass / Wait checks:
volume, VWAP, 5m trend, 15m trend, recent movement, sector and market alignment.
The volume check needs the stricter Booster defaults (RVOL >= 1.5 plus twenty
baseline sessions). 15m trend, recent movement, sector and market may still wait.
Opposite/balanced but fresh market context can therefore produce a building
watchlist with Market = Wait; that stock cannot yet qualify for a Booster.

Ranks sort by checks passed descending, gap ascending, then symbol. These are
rule counts, NOT probabilities. Even 7/7 means the breakout is still unconfirmed;
there is no stop, target, automatic order or entry recommendation in these cards.
A subsequent confirmed breakout must independently pass the Booster rules.

Snapshots expire at the earliest stock/context quote deadline, API-cache limit,
next 5m candle boundary or 14:45 cutoff. The API and browser remove stale/expired
building states. Panels respect all stock filters; fewer than five is valid.
No synthetic fallback fills an empty watchlist.

## Session Buy / Sell Pressure

Section 08 now ranks the WHOLE current session, not only the latest candle.
Coverage begins at 09:15 IST and includes EVERY completed 5m candle through the
current cutoff. First display is possible at 09:20, when the first candle closes.
At 11:02, for example, coverage is 09:15-11:00; 11:00-11:05 is still forming and
excluded. The session resets each trading day and the live list ends at 15:30.
Polling targets three seconds, but totals change only when completed candles or
broker backfills change. Missing coverage/seeding can delay or pause the panel.

### Cumulative calculation

For each bar, `f = (close - low) / (high - low)`, or 0.5 for a flat range.
Estimate buy volume as `volume x f` and sell volume as `volume x (1 - f)`.
Add those estimates across ALL completed current-session bars, then calculate:

`session pressure % = 100 x (sum estimated buy - sum estimated sell) / sum volume`

This is volume-weighted, NOT an average of individual candle percentages. Zero-volume
bars contribute no volume; the session needs positive total volume. A previous
session never contributes. Buy/sell side follows this cumulative percentage, not
the last candle or the stock's price change. Actual executed buying/selling and
aggressor-classified trade delta remain unavailable: these estimates do NOT prove
real institutional accumulation or distribution.

### Ranking and cards

- Up to five per side: absolute cumulative pressure descending, then weighted
  rule score descending, then symbol ascending. No latest-candle ranking substitute.
- Default minimum absolute session pressure is 20% (`IMBALANCE_MIN_PCT`).
  Fresh source AND receipt times, valid uncrossed two-sided depth, eligible
  liquidity and complete trusted current-session candles are mandatory.
- Latest-bar pressure, resting-depth direction, volume spike and historical
  baseline availability are NOT session-watchlist gates. A strong session can
  remain visible during a weak/opposite latest candle or without a volume spike.
- Cards show coverage, completed-bar count, cumulative estimated buy/sell/net
  volume, previous cumulative percentage, percentage-point change, current resting
  depth and latest completed 5m pressure separately. Resting depth is a current
  snapshot, not a historical accumulation of book orders.
- Strengthening/Cooling compare absolute cumulative percentages with the prior
  completed cutoff; below 0.01 pp is Steady. First bar and Changed side are explicit.
- All-broker history is broker-confirmed source quality. Any sampled/mixed session
  remains provisional and cannot alert; unknown sources or missing slots cannot rank.

The separate `session-pressure-v1` score retains eight weights: depth 20, cumulative
session pressure 15, latest 5m volume spike 15, and VWAP/momentum/new breakout/
consecutive candles/independent sector peers 10 each. Unknown optional checks score
zero without renormalization. The score is not a probability and is secondary to
cumulative pressure in ranking. Early momentum/opening-range checks remain Unknown
until enough completed bars exist; they are not fabricated.

### Session alerts

Opt-in in-app alerts now use the displayed session rankings. Arming and changed
filter scopes baseline existing IDs without replay; only new eligible IDs in the
filtered top five per side alert. IDs deduplicate in memory for the current page,
access session and day; reloading does not preserve this event history.

Confirmation requires all eight checks, all-broker current-session coverage,
direction-aligned latest 5m pressure at the configured threshold, a NEW completed
opening-range crossing, price still beyond the trigger and the live 90-second TTL
(or a shorter configured TTL). Earliest possible confirmation is 09:35. This is
observation, not an entry or order. No sound, browser notifications or external
sending. Stale/offline/expired data remove candidates and pause alerts.

The API adds `row.session_pressure`; legacy `row.imbalance` remains unchanged for
existing consumers. The private `imbalance-alerts-YYYY-MM-DD.jsonl` log still records
legacy 5m confirmations only, NOT the new session in-app events. History cache
schema stays v3; no old unknown provenance is promoted to broker-confirmed data.

## Legacy 5m API

The following definitions apply to the preserved `row.imbalance` backend payload,
not the default Section 08 ranking. Its stricter latest-bar gates remain separate.

This is an experimental observation watchlist, NOT executed order flow, an entry
recommendation or an accumulation detector. Intraday / individual stocks only;
Regular mode and custom baskets cannot qualify. It starts after the completed
09:15-09:30 IST opening range and can run until 15:30, unlike the Booster cutoff.
Each side shows at most five qualifying stocks, sorted by weighted score descending,
then absolute estimated candle imbalance descending, then symbol ascending.

### Observed depth versus estimated pressure

- **Observed resting depth:** sums valid positive quantities from up to the first
  five broker bid levels and five ask levels. Quantity imbalance is
  `100 x (bid_quantity - ask_quantity) / (bid_quantity + ask_quantity)`.
- **Observed notional depth:** price x quantity at each valid level. The separate
  notional imbalance uses the same difference-over-total formula with bid/ask
  notionals. It is NOT the quantity imbalance used by the pressure gate/score.
  Resting orders can be cancelled; neither measure represents executed trades.
- **Estimated candle pressure:** for the latest completed 5m candle, let
  `f = (close - low) / (high - low)`. Estimated buy volume is `volume x f`, sell
  volume is `volume x (1 - f)`, and imbalance is `100 x (2 x f - 1)` for positive
  volume. A flat range is neutral (50/50) and cannot meet the default pressure gate.
  These are close-location estimates, not classified buying/selling executions.
- Actual executed buy volume, sell volume and trade imbalance are unavailable
  (`null`). Total candle volume does not supply the missing execution-side split.

Candle provenance summarizes ALL completed current-session bars, not just the
latest one. All `broker_history` bars produce `broker_confirmed` quality. Any
`sampled_ticks` or `mixed` sequence has sampled quality and stays **provisional**,
even at 8/8; it can NEVER alert. Missing/unknown sources cannot qualify. Completed
broker backfills replace sampled bars and may subsequently permit confirmation.

### Data and eligibility

The panel requires a live, fresh API cache, current-session stock quote and depth
source/receipt timestamps and liquidity eligibility. Pressure uses its own
completed-bar features, not the unrelated RSI/ADX indicator-readiness gate. Every
completed current-session 5m slot from 09:15 through the current clock cutoff must
exist and contain valid OHLCV; a forming, missing or older latest bar cannot qualify.
Both resting QUANTITY imbalance and estimated candle imbalance must align with the
pressure side and reach at least 20% by default. Pressure side comes from the candle
estimate; the UI direction filter still uses price change from the session open.

The 5m volume spike is the latest completed candle's volume divided by the MEDIAN
of valid broker-history bars at that identical clock-time slot on earlier dates.
It uses up to 20 prior samples (`VOLUME_BASELINE_SESSIONS`), with at least ten
required by default (`MIN_VOLUME_SESSIONS`), and needs at least 1.5x volume. Valid
zero-volume baseline bars count; missing, invalid or non-broker bars do not. A
missing or zero median cannot supply a qualifying ratio. This is NOT cumulative
intraday RVOL and does not use the Relative Volume panel's cumulative baseline.

The pressure VWAP is a completed-current-session HLC3 volume-weighted estimate,
where `HLC3 = (high + low + close) / 3`. The live broker average-price VWAP is shown
separately, when available, and does not replace the closed-bar pressure checks.
Independent sector peers use configured primary-sector membership, exclude the
subject stock, and require at least 80% fresh peer quote coverage (source AND receipt).
Their mean change from the session open must align with pressure to pass. This peer
calculation is independent of UI filters; insufficient coverage is Unknown, not a
directional pass. Pressure does not require a matching NIFTY market-context regime.

### Fixed pressure-v1 score

Eight fixed checks contribute their weights only when passed:

| Check | Weight | Pass condition |
| --- | ---: | --- |
| Quoted depth | 20 | Direction-aligned resting quantity imbalance meets the threshold |
| Estimated candle pressure | 15 | Direction-aligned close-location estimate meets the threshold |
| 5m volume spike | 15 | Same-slot bar volume ratio meets the threshold with sufficient baseline samples |
| Closed-bar VWAP | 10 | Latest close is strictly on the pressure side of the HLC3 VWAP estimate |
| Momentum | 10 | Three-bar close return and change in the closed-bar VWAP estimate both align |
| Completed breakout | 10 | Latest completed close newly crosses the opening-range boundary |
| Consecutive candles | 10 | At least two consecutive direction-aligned candle bodies |
| Independent sector peers | 10 | Known peer mean aligns with pressure at >= 80% coverage |

The first three checks are mandatory for any card. Failed/Unknown checks contribute
zero, without reweighting. `pressure-v1` is a 0-100 weighted rule score, NOT a
calibrated probability, trading accuracy estimate or expected return.

### Confirmation, alerts and settings

**Confirmed** requires all 8/8 checks, an entirely broker-confirmed current-session
sequence, and the LATEST completed 5m close strictly above the opening-range high
for buy pressure or below its low for sell pressure. The preceding close must be
at or inside that same boundary: an older breakout is not a new crossing. Current
price must also remain strictly beyond the trigger in that direction. Confirmation
can first occur after the fourth 5m bar closes, at 09:35 IST.

Alert eligibility expires no later than 90 seconds after that candle closes by
default (`SIGNAL_TTL_SEC`), and earlier if quote/depth/receipt, known sector peers,
snapshot/API cache, current completed-slot coverage or session validity expires.
An 8/8 broker watch card outside this window or without price beyond the trigger
is not alert-eligible. Offline, seeding, disconnected, stale or expired data cannot
alert, and no historical snapshot fallback is used for this panel.

The current in-app alerts use the session payload described above, not this legacy
5m payload. Event history is not a current signal or entry recommendation.

Backend settings are `IMBALANCE_MIN_PCT=20` (both quantity depth and estimated
candle pressure) and `IMBALANCE_MIN_BAR_RVOL=1.5` (same-slot bar volume).
The browser reads these thresholds and `MIN_VOLUME_SESSIONS` from the API so
displayed gates match the configured pressure rules. It additionally retains
conservative maximums of 20 seconds for quote/depth/known-peer ages, 30 seconds
for cache/computation and 90 seconds for alerts. Wider backend freshness/TTL
settings do not widen those browser safety limits; shorter server deadlines apply.

The server independently records eligible confirmations across its computed stock
universe, regardless of in-app opt-in or UI filters, to the PRIVATE research log
`SCANNER_DATA_DIR/imbalance-alerts-YYYY-MM-DD.jsonl`. Records include version,
publication time, symbol and pressure checks, not phone numbers or orders. Keep
these files private and manage retention yourself. Logged confirmations need not
be displayed or generate an in-app alert because filters, opt-in and browser
safety limits still apply. Deduplication is an in-memory
process-session ID set, NOT restart-proof: IDs are not restored from JSONL, so a
restart can append a duplicate eligible event. Logs alone do not establish fills
or profitability.

## Experimental Booster Rules

These are deliberately selective, unvalidated research rules, not advice:

- A completed five-minute close crosses the first 15-minute opening range.
- Fresh stock/market data, complete current-session bars and ready indicators.
- Twenty valid volume baseline sessions and RVOL at least 1.5 by default.
- Recent movement, VWAP, 5m/15m trend, sector and NIFTY context align with direction.
- Liquidity eligibility is required. No new candidates after 14:45 IST.
- Stop is the breakout candle's opposite extreme; initial risk is 0.10%-1.00%.
- Price must remain within 0.5 times initial risk beyond the trigger.
- Illustrative target is 1.5 times initial risk beyond the trigger; expiry is
  90 seconds after the confirmation candle closes. Levels are not fill assumptions.

No qualifying setup is a valid result. The old RFactor and resting-book score
contribution to momentum were removed rather than carrying over their misleading
semantics.
Momentum score is an explicit experimental 0-100 composite: movement 25%,
time-matched volume 25%, closed-bar continuity 20%, aligned ADX trend 20%, recent
directional movement 10%. It is not a calibrated probability.

First eligible events are logged once per setup ID within the server process to
`SCANNER_DATA_DIR/signals-YYYY-MM-DD.jsonl` with publication time, prices,
components, rules and version. Reappearing refreshes are not new signals within
that process; deduplication is not restored from logs after a restart.
Log retention/rotation is your responsibility; logs contain no phone numbers.

Use `evaluate_trades.py` on actual paper/live fills to measure cost-adjusted
results. Signal logs alone do not establish profits. Validate chronologically
on unseen periods; compare against simpler mover/RVOL baselines, and report
sample size, filled-pick hit rate, net expectancy, drawdown and unfilled triggers.

## Deploy on Render

Put the extracted project's FILES at the repository root. Use the supplied
`render.yaml`. If keeping this folder nested, set Render's Root Directory to
its actual repository path. Do not leave it pointing at the old `outputs` folder.

```bash
python3 -m pip install -r requirements.txt
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 8 --timeout 120
```

Keep ONE worker: it owns the market stream, caches and access locks. Choose an
always-on instance with adequate CPU/memory. Add private `KITE_API_KEY` and
`KITE_ACCESS_TOKEN` environment variables; renew the token when Kite requires it.
The health endpoint is `/api/health`; it exposes no allowlist or broker secrets.

### Configure the private access list

`numbers.txt` is included in the downloaded ZIP but excluded from Git. A normal
Git-based Render deploy therefore may not contain your local copy. Do not remove
the privacy protection or make the file publicly downloadable.

1. In Render, select your actual scanner Web Service and click **Environment**.
2. Under **Secret Files**, click **+ Add Secret File**.
3. Set Filename to exactly `numbers.txt` (lowercase). Paste your approved numbers
   in Contents, one 10-digit number per line. An optional `+91` prefix is accepted.
   Blank lines and `#` comments are ignored; do not put multiple numbers on one line.
4. Set environment variable `ACCESS_NUMBERS_FILE` to `/etc/secrets/numbers.txt`.
   This path is already declared in the updated `render.yaml` for Blueprint use.
5. Save Changes / Save and deploy, wait for the deploy to become live, and reload
   the site. If you also changed source code, deploy the latest source commit.

Without an explicit path, the updated loader prefers `/etc/secrets/numbers.txt`,
then the `numbers.txt` beside the Python application. Relative configured paths
are relative to that application directory, not Gunicorn's working directory.
An explicit missing/unreadable path does NOT fall back to another list. An empty
or unreadable selected file also disables access rather than admitting anyone.

The new login response distinguishes `access_not_configured` (HTTP 503: no usable
list) from `not_allowed` (HTTP 403: a list was loaded but this number is absent).
Private server logs identify the file/configuration problem without printing
phone numbers. `/api/health` reports version `premium-2.4-session-pressure` for this build.

Render's instructions: https://render.com/docs/configure-environment-variables#secret-files

Attach a persistent disk and set `SCANNER_DATA_DIR` to its writable directory
if you want history and research logs to survive service restarts. Otherwise
`.runtime/` is local/ephemeral. History cache format is compressed JSON schema
version 3, including candle provenance, NOT pickle. Older schema caches and old
pickle caches are intentionally not loaded; an old cache requires a fresh broker
history seed. The initial full-universe seed is paced and can take several minutes;
candidates remain disabled during seeding.
Reconciliation is paced, defaults to every 180 seconds after each sweep, and
continues briefly after the close to capture the completed final candle.

## Tests and Limits

```bash
python3 -m unittest discover -s tests -v
```

Tests use isolated synthetic fixtures, no real credentials/network or allowlist.
They validate data correctness and gate behaviour, NOT trading profitability.
An independent run for this version verified that all 220 Python tests passed.
This documentation-only update does not claim browser tests passed or live-market
integration was verified.
Live-market integration and a cost-adjusted historical strategy backtest still
need your valid data subscription and real observations. No official exchange
holiday/corporate-action calendar or complete trade tape is bundled. Weekend/
hour checks do not assert that an exchange is trading: fresh quotes and coverage
must also exist. Maintain the configured stock/index membership over time.
