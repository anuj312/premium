# Pulse Premium

A clean NSE research terminal with real-data-only watchlists, experimental
intraday booster candidates, liquidity leaders and pre-breakout building watchlists.
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
- All panels respect direction, volatility and search. Sector flow is a server
  aggregate of the selected universe/sector, independent of those local filters.
- Market context always uses the configured NIFTY 50 membership, independent
  of the UI selection. Constituent definitions are supplied by the server.
- Custom baskets are equal-weight statistics, not official index prices.
  They do not have executable LTPs or liquidity/booster/building candidates.

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
- VWAP uses the current-session broker average traded price when available,
  otherwise a completed-bar typical-price approximation. It never substitutes
  yesterday's VWAP for today's live context.
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
- Each candidate expires on stale stock quotes, stale market context, stale API
  cache, session change or its own expiry. Browser requests have timeouts and
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
contribution were removed rather than carrying over their misleading semantics.
Momentum score is an explicit experimental 0-100 composite: movement 25%,
time-matched volume 25%, closed-bar continuity 20%, aligned ADX trend 20%, recent
directional movement 10%. It is not a calibrated probability.

First eligible events are logged once per setup ID to
`SCANNER_DATA_DIR/signals-YYYY-MM-DD.jsonl` with publication time, prices,
components, rules and version. Reappearing refreshes are not new signals.
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
phone numbers. `/api/health` reports version `premium-2.2-building` for this build.

Render's instructions: https://render.com/docs/configure-environment-variables#secret-files

Attach a persistent disk and set `SCANNER_DATA_DIR` to its writable directory
if you want history and research logs to survive service restarts. Otherwise
`.runtime/` is local/ephemeral. Cache format is compressed JSON, NOT pickle.
Old pickle caches are intentionally not loaded. The initial full-universe seed
is paced and can take several minutes; candidates remain disabled during seeding.
Reconciliation is paced, defaults to every 180 seconds after each sweep, and
continues briefly after the close to capture the completed final candle.

## Tests and Limits

```bash
python3 -m unittest discover -s tests -v
```

Tests use isolated synthetic fixtures, no real credentials/network or allowlist.
They validate data correctness and gate behaviour, NOT trading profitability.
Live-market integration and a cost-adjusted historical strategy backtest still
need your valid data subscription and real observations. No official exchange
holiday/corporate-action calendar or complete trade tape is bundled. Weekend/
hour checks do not assert that an exchange is trading: fresh quotes and coverage
must also exist. Maintain the configured stock/index membership over time.
