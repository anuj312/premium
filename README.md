# NEONFLOW V12 — PRO SCORE (0–100) + Kite Live Market Map

A read-only **dark-neon trading dashboard** for NSE stock symbols, sectors, and nearest-expiry FUTSTK contracts. Its visual sector-to-stock links show **relative price strength**, not executions or institutional cash flows. No order-placement endpoints are enabled.

## V12 — professional bullish / bearish score engine

The dashboard now **defaults to PRO SCORE / 100**. Top 10 bullish and top 10 bearish tiles are ranked by the score **descending on both sides** (the score measures strength *in that direction*). Unscored stocks appear behind scored stocks and show `—`. The dropdown retains **20D momentum ×** and **Daily % Change** for comparisons.

Scores are **sampled on completed 5-minute NSE cash candles**, while the KiteTicker price itself continues updating. Scoring starts after **12 completed 5-minute bars** (10:15 IST); live mode does not fake a score before there is sufficient evidence. The refresh worker paces historical API calls and keeps Render available during loading. The 20-day baseline loader and 5-minute volume-history loader are independent, and both are paced through a shared history-request lock. On a cold start, loading ~198 NSE stock histories can take several minutes.

| Component | Maximum | Rule (direction-sensitive) |
|---|---:|---|
| 20D price momentum | 20 | Completed 5m close vs prior NSE close, normalized by previous 20-session avg absolute daily move; max credit at 3× |
| Time-matched RVOL | 20 | Today's cumulative **completed-5m** volume vs average cumulative volume through same 5m time bucket on 20 prior complete sessions |
| Trend efficiency/persistence | 20 | 12 points efficiency of close-to-close last 60 minutes; 8 points for aligned 15/30/60m direction |
| VWAP | 10 | Close in favorable direction vs candle-based typical-price/volume session VWAP |
| EMA50 | 10 | Close alignment and slope of 5-minute EMA50 |
| Nearest FUTSTK OI | 10 | Same-contract **futures price** and OI direction since app connected; missing futures OI earns 0 and is flagged |
| Sector strength | 5 | Cash sector's daily % supports selected direction |
| Breakout/breakdown | 5 | Closed 5m price exceeds last 12 prior 5m highs/lows |

The sum is capped at 100, with a **liquidity caution** (low completed-volume) and a **VWAP overextension penalty**. Components are visible in the stock inspector. Missing 20D price or 20-session volume baselines, too few 5-minute bars, or missing EMA50 warmup => no score. The OI item only gets credit for properly aligned *futures-price* and *futures-OI* changes on the same comparison basis; otherwise it is flagged. All weighting thresholds are heuristic and not backtested, and the score is **not a prediction or verified institutional-flow reading**.

**Data scope:** The trading chart remains **NSE CASH**, with EMA50 and order-block candidates displayed; the only futures element is the OI confirmation metric and the nearest FUTSTK quotes. Do not treat this as a futures-candlestick view. Demo data is synthetic, including demo 20-day references.

**API fields:** `stocks[].pro_score`, `.pro_direction`, `.pro_components`, `.pro_rvol`, `.pro_efficiency`, `.pro_persistence`, `.pro_asof`, `.pro_coverage`, `.pro_flags`, `.pro_reason`; `meta.proscore` contains loading status, count and coverage.

## Run on your Mac

```bash
unzip KiteNeonFlow_V12_ProScore.zip
cd KiteNeonFlow_V12_ProScore
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. The default `MODE=demo` starts with **synthetic** prices and candles so you can inspect everything without a Kite account.

### Switch to real Kite live ticks

1. Create a paid Kite Connect API app at https://developers.kite.trade and configure its redirect URL. Live quotes/history require the relevant subscription/entitlements.
2. Edit `.env`:

```ini
MODE=demo
FEED=ticks
KITE_API_KEY=YOUR_API_KEY
KITE_API_SECRET=YOUR_API_SECRET
KITE_ACCESS_TOKEN=
POLL_SECONDS=2
```

3. Stop the running server and run `python auth.py`. Open its login URL; paste back the resulting **request_token** or redirected URL. This stores the session token in `.env` and changes the mode to `live`.
4. Restart with `uvicorn main:app --host 127.0.0.1 --port 8000` (one worker only). Check `/api/state` for `meta.feed`, `meta.feed_state`, `meta.received_ticks` and `meta.last_tick_at`.

**Do not share your API secret, access token or request token.** Kite access tokens usually need a fresh login each trading day. An expired token does not trigger simulated-data fallback.

## What's new in V7 — TradingView-inspired chart

Open any stock card. The inspector chart fills the old metrics-grid space. It includes:

- **Timeframes**: **1m, 3m, 5m (default), 15m, 30m, 1D**. The backend requests the corresponding Kite historical-data interval. Chart cache is **separate per symbol and timeframe** (45 seconds).
- **EMA 9 / 20 / 50**: switchable colored moving averages computed from loaded candle closes. EMA starts only after enough candle history exists.
- **Session VWAP**: calculated from typical price `(H+L+C)/3 × reported volume` and reset per NSE trading day. **Approximation**, not true tick VWAP. Disabled on daily charts.
- **PDH / PDL / OPEN**: prior trading session's high/low and latest loaded session's opening price, with toggle. "Previous day" refers to the previous *trading* session present in the loaded candles.
- **S1/S2/S3 and R1/R2/R3**: price-action swing-derived support and resistance candidates from the currently visible candle window, with toggle. Not confirmed market orders.
- **Order-block zones**: shaded bullish and bearish candidate zones on the selected timeframe. The existing swing-breakout rule is unchanged and detects zones using **completed** historical candles, not the currently forming bar.
- **OHLCV strip**: shows Open, High, Low, Close, and volume for the hovered candle, with IST date/time; reverts to most recent bar outside the chart. Crosshair labels appear on the time and price scales.
- **Volume bars**, mouse-wheel zoom, drag-to-pan, and live current-candle OHLC changes when KiteTicker receives fresh symbol ticks.

### Data behavior and limits

- `GET /api/chart/MPHASIS?timeframe=5m` supplies chart candles and candidate OB zones. Other valid `timeframe` values: `1m`, `3m`, `15m`, `30m`, `1D`.
- The backend uses only **NSE cash OHLC history** for candlesticks and overlays. Futures OI comes from nearest FUTSTK in the separate live market engine.
- Live ticks can update **current candle price, high and low**, but the live candle's volume is **not reconstructed accurately from KiteTicker**. New tick-only candles begin with volume zero; VWAP may therefore be incomplete until history refresh. Reopen the chart to refresh stored historical volume and order-block candidates.
- Live chart history is retrieved when you select a stock/timeframe, not every browser tick. Kite API subscription or limits can prevent a particular chart from loading. Errors show a visible message and never switch live mode to synthetic candles.
- 30-minute bars are anchored at **09:15 IST** (the NSE session open), not an arbitrary wall-clock half-hour. The last bar of the day completes at market close.
- The historical order-block rule requires an opposite-color candle followed by a strong breakout of a preceding 6-bar swing, with body/displacement thresholds. Later *completed* closes can invalidate its zone. Zones do not prove institutional buying or selling.

## Background server architecture

```text
Kite instrument master (NSE + nearest-expiry NFO FUTSTK)
  → initial Kite REST quote snapshot
  → KiteTicker WebSocket FULL mode → live token-indexed cache
  → MarketEngine sector breadth / OI-build-up / movers
  → FastAPI /ws pushes dashboard snapshots every POLL_SECONDS

On stock click/timeframe switch:
  → FastAPI /api/chart/{symbol}?timeframe=…
  → Kite historical_data() (or synthetic demo)
  → price-action zones + interactive Canvas chart
```

Use a **single Uvicorn worker**: extra workers each create their own Kite ticker connection and separate tick caches.

## Project files

- `main.py`: FastAPI state/WebSocket, chart history endpoint and interval validation
- `market.py`: KiteTicker / REST providers and market breadth
- `charting.py`: timeframes, synthetic candles, OHLC normalization and order-block detection
- `sectors.py`: original NSE symbol universe
- `auth.py`: Kite login helper
- `static/index.html`, `style.css`, `app.js`, `chart.js`: responsive neon UI, breadth dominance meter and interactive chart
- `tests/`: market, ticker, order-block and timeframe tests

Run tests (install `pytest` if it is not available): `python -m pytest -q tests`.

## Security

Keep `.env` private; it is gitignored. Do not publish this server publicly without authentication, secure WebSocket connections and suitable permissions for Kite market data. This is a visualization tool, not investment advice or an auto-trader.


## V9 Render asynchronous startup

The website launches first; Kite market data seeds in the background. The dashboard shows loading stages, and `/healthz` remains HTTP 200 while loading. See `RENDER_DEPLOY.md`.


## V10: 20-trading-day relative momentum

- For each NSE equity, compute **average absolute close-to-close % change from the previous 20 completed trading sessions** (requires 21 prior daily closing prices).
- Live score `momentum_20d_x` is **signed** `today's % change vs previous NSE close / 20-day average absolute daily % change`. Example +3% today / 1% typical = +3.0x. -3% => -3.0x. This is a normalization score, not a 20-day gain or future prediction.
- 20D baselines load **after** market seeding and web startup; REST history is paced sequentially across ~198 symbols. The dashboard remains usable; displays live progress, and uses daily % change ranking until baseline loading completes. Some symbols can remain unavailable if Kite history fails or lacks enough sessions. On success, leaderboards prioritize 20D-normalized price movement, preserving separate gainers/losers. Sector rows show their equal-weight mean signed 20D score (while daily % remains visible), with sorting by that measure.
- Demo mode uses **simulated baselines** explicitly; no Kite historical requests.
- On next calendar date in IST, 20D baselines are recalculated. No persistence across Render restarts. A paid Kite historical-data subscription and valid token are required in live mode. No volume normalization or same-clock RVOL is calculated by this update.


## V11: Momentum-first bull/bear leaderboards

- **Bullish** and **Bearish** lists now show a large signed **MOM 20D** score on every card and retain the daily price % change below it.
- Default sorting once 20-day histories are ready: **highest positive score first** for bullish stocks, **most negative score first** for bearish stocks. Scores are measured in **× multiples**, not 0–100 points.
- Formula: `momentum_score = today's NSE cash % change / mean(abs(daily close-to-close % change)) over previous 20 completed trading sessions`. This score quantifies unusual price movement, **not guaranteed future direction or institutional trades**.
- During historical seeding, ranking falls back to **daily % change** until the 20-day process completes. Missing or invalid 20D history displays `—`; missing scores appear **below valid scores**, and are sorted by daily percentage within that group.
- The rank selector lets you switch between **20D MOMENTUM SCORE** and **DAY % CHANGE**. The API now exposes `momentum_score` (also retains `momentum_20d_x`).
- Do not calculate a score if the 20-day baseline is missing or zero. No fake rank or historical price is substituted for unavailable live Kite history.
- Deploy on Render using the existing single-worker start command and your private Kite credentials in Render Environment settings.
