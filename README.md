# NEONFLOW V7 — Kite Connect Live Market Map

A read-only **dark-neon trading dashboard** for NSE stock symbols, sectors, and nearest-expiry FUTSTK contracts. Its visual sector-to-stock links show **relative price strength**, not executions or institutional cash flows. No order-placement endpoints are enabled.

## Run on your Mac

```bash
unzip KiteNeonFlow_V7.zip
cd KiteNeonFlow_V7
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
