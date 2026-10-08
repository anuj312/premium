# KiteNeonFlow V7 on Render

The current project is a FastAPI web application with an inbound browser WebSocket (`/ws`) and an outbound Zerodha KiteTicker connection in live mode. `static/` is served by FastAPI; **do not** create a Render Static Site.

## 1. Test in demo mode first

1. Push the **contents of this project folder** to a **private GitHub repository**. Keep `.env` uncommitted; the `.gitignore` excludes it.
2. Render dashboard -> **New -> Web Service** -> connect the repository. Choose **Python 3**.
3. Use **Build Command**: `pip install -r requirements.txt`.
4. Use **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
5. If the repository root contains `main.py` and `requirements.txt`, leave **Root Directory** unset. If those files are within a subdirectory, set Root Directory to that subdirectory.
6. In Render **Environment**, set `MODE=demo`, `FEED=ticks`, `POLL_SECONDS=2` (no Kite credentials required). Deploy and open the assigned `https://<your-service>.onrender.com/` URL.
7. Optionally, use **New -> Blueprint** to deploy from `render.yaml`. It selects a free service for demo use. You can upgrade the instance after testing.

Free instances can go idle and sleep. They are not reliable for uninterrupted live market monitoring. Configure one always-on Render web service instance if continuous operation is essential.

## 2. Switch to live Kite data (private, authorised access only)

The **existing code has no user login/access-control layer**. Anyone knowing your Render URL could read data via `/api/state`, `/api/chart/...` and `/ws`. Do not make the `MODE=live` service accessible to the public without implementing authentication/authorisation and verifying your data-use rights. Kite Connect terms restrict public display and redistribution of live data.

1. On **your Mac**, set up local `.env`:

   ```ini
   MODE=demo
   FEED=ticks
   POLL_SECONDS=2
   KITE_API_KEY=your_key
   KITE_API_SECRET=your_secret
   KITE_ACCESS_TOKEN=
   ```

2. Install dependencies (`pip install -r requirements.txt`), then run `python auth.py` locally. Open its login URL, authenticate to Zerodha, and paste the redirected `request_token` (or redirect URL) into the terminal. It writes the new access token in local `.env`.
3. On Render -> your service -> **Environment**, set:

   ```ini
   MODE=live
   FEED=ticks
   POLL_SECONDS=2
   KITE_API_KEY=<your API key>
   KITE_ACCESS_TOKEN=<new token from local .env>
   ```

   `KITE_API_SECRET` is **not** required by the running server; keep it on your Mac for `auth.py`.
4. Select **Save and deploy**. Render restarts with the new values. `auth.py` is NOT an unattended login helper; do not add it to Render's build or start command.
5. Check `https://<your-service>.onrender.com/api/state` in a *secured* deployment. For `MODE=live`, successful tick updates should show `meta.feed="ticker"` and `meta.feed_state="streaming"`; `meta.received_ticks` should increase during trading. Outside market hours, `waiting_for_ticks` or `market_closed_or_idle` is normal.
6. Repeat local login and update `KITE_ACCESS_TOKEN` in Render **each trading day**. Zerodha tokens generally expire at **6 AM IST the following day**. This app has **no automatic token rotation**.

### Why one worker?

Only one Uvicorn worker and one Render instance should run this in-memory architecture. Each worker/instance otherwise starts its own KiteTicker WebSocket and independent caches. Scaling to multiple instances needs an external streaming/broadcast layer (e.g. Redis pub/sub) and proper licensing.

### Chart data source

In V7, NSE cash candles/indicator levels render in the chart. Nearest-expiry futures OI is shown separately. This version does **not** have a futures-candle toggle.

## Common problems

| Symptom | What to check |
|---|---|
| App fails to start | Build/start commands; `MODE` and access token; Render **Logs**; missing entitlement or network issue when loading Kite instruments |
| `KiteTicker` won't connect | Token expiry, API subscription/entitlements, Kite Connect connection status |
| Page works in demo, but live fails | Supply both `KITE_API_KEY` and fresh `KITE_ACCESS_TOKEN`; then **Save and deploy** |
| Charts don't load | Historical-data subscription, rate limits, and `/api/chart/{symbol}?timeframe=5m` |
| No ticks outside market hours | Expected; verify at NSE market hours, not from a demo snapshot |
| Free service wakes slowly | Free Render service spin-down; upgrade for always-on operation |

Useful links:
- https://render.com/docs/deploy-fastapi
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/free
- https://kite.trade/docs/connect/v3/user/
- https://kite.trade/terms/
