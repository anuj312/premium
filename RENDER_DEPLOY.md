# V12 Async Seeding + Pro Momentum 0–100 — Render deployment

The page and `/healthz` become available **before** NSE/NFO instrument downloads, first quotes, or KiteTicker connect. Kite loads on a background task after Uvicorn has started.

## Render Web Service settings

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health check: `/healthz` (HTTP 200 while seeding, and if seeding fails)
- Set environment variables in Render: `MODE=live`, `FEED=ticks`, `POLL_SECONDS=2`, `KITE_API_KEY`, `KITE_ACCESS_TOKEN`. Initially test with `MODE=demo`.
- Keep your Kite API secret and access token out of GitHub. Token must be renewed as required by Zerodha.
- Use an always-on paid instance for continuous live ticks; free Render instances may sleep.

## Expected output

Render can report a deployed server while `/api/state` shows:

- `status: starting`, `meta.feed_state: seeding`, `meta.seed_stage: LOADING NSE & NFO INSTRUMENTS`
- `... SEEDING INITIAL MARKET QUOTES`
- `... PREPARING MARKET DASHBOARD`
- eventually `status: ok` with live stock data, even if markets are closed (ticker waits for ticks)

The browser displays a **SEEDING MARKET DATA** overlay and stage progress. Once seeding finishes, the market map appears without a page reload. On a failed seed, Render stays online and the dashboard shows **MARKET SEED FAILED**, with a generic diagnostic; inspect Render Logs for details. `/api/chart/{symbol}` returns HTTP 503 during seeding.

**Note:** the market data is still the cash chart; futures OI comes from nearest-expiry futures where available. Historical charts only load when a stock is selected. WebSockets and in-memory state use a single Uvicorn worker.

## 20D historical baseline after startup
The site and live ticks load first. Once ready, V11 fetches 65 calendar days of NSE daily candles per symbol in a **separate paced background task**, excluding today's candle, to compute average absolute daily close-to-close % change across the prior 20 completed sessions. **The server does not block on these requests.** The right panel displays `20D SEEDING n/total`, then `20D READY`; it falls back to daily %-change ranking when loading is unfinished or historical data is unavailable. Live data requires historical_data access in your Kite plan.

One worker remains required: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`.

## V11 stock score order

After 20D history seeding is ready, **Bullish / Top Momentum** ranks `momentum_score` descending (+4.0x ahead of +2.0x), while **Bearish / Top Momentum** ranks it ascending (-4.0x ahead of -2.0x). The 20D average includes only completed previous sessions. Cards show both score and day change. Missing scores sort last, and daily-% fallback applies until seeding completes.

## V12 Pro Score 5-minute seeding
After the website and live ticks are serving, an independent background worker reads 5-minute historical candles for each stock. Its first pass pulls up to 45 calendar days and builds 20 prior full-session, **same-time cumulative-volume** reference curves. Only a small reference curve + EMA50 warmup is retained; subsequent 5-minute refreshes query just the latest current-session bars. The worker does not delay Uvicorn binding to `$PORT`. It displays `PRO 5M n/198` status and does not invent missing values. A separate worker provides the 20D daily price-move baseline. Both workers serialize Kite history requests through a shared throttle. Real data needs the Kite historical-data entitlement and a valid session token.

Select **PRO SCORE / 100** in the ranking dropdown. The score measures bullish and bearish quality separately; the strongest of each appears first. Score computation begins once there are at least 12 completed five-minute bars for the trading day and a valid 20-day reference. During seeding, price tiles and health checks continue functioning, with `—` for incomplete scores.
