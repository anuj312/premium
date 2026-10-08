# V9 Async Seeding — Render deployment

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
