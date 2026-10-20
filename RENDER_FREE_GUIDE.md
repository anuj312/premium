# Render Free deployment — OrderFlow Studio Premium (20-day edition)

This release preserves the existing dashboard CSS, logo, animated wires, header,
top-ten momentum cards, top-three picks, and 5m/15m/30m/1h chart selection.
Only the server's CPU, memory, history-loading, caching, and retry behavior have
changed. **It is a best-effort live scanner, not an always-on production service.**

## Deploy without changing plan

1. Download this ZIP, extract `orderflow-studio/`, and commit these project files
   into the repository used by your *existing* Render service. Do not commit `.env`.
2. Render > your Web Service > **Environment**:
   - `MARKET_MODE=kite`
   - `KITE_API_KEY` (private, from your own Kite app)
   - `KITE_ACCESS_TOKEN` (private, current Zerodha session)
   - Optional `MAX_CHART_SYMBOLS=2`
   - Optional `HISTORY_CALL_SPACING=0.55`
   - Optional `HISTORY_SYMBOL_PAUSE=0.20`
   - Optional `HISTORY_MAX_ATTEMPTS=2`
   - Optional `CHART_HISTORY_REFRESH_SECONDS=600`
3. Render settings (or included `render.yaml` on a **new** Blueprint):
   - **Instance type:** Free
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Workers:** 1 (never launch two copies of the in-memory feed)
   - **Root directory:** empty if `server.py` is in repo root; otherwise `orderflow-studio`
4. Deploy latest commit, then open `/api/health` and dashboard `/`.

## What "live" means

- `/api/health`: `mode=kite`, `feedStatus=connected` reports a connected Kite
  WebSocket, **not** whether the NSE market is open. `marketStatus=LIVE` requires
  fresh cash quotes during normal weekday hours, and `lastQuoteAt` must advance.
- Top-ten and top-three scores become 20-day-informed as individual baselines
  finish. A partially loaded history is labelled; **RVOL is not invented**.
- Background history loads 20 completed trading sessions per equity in a single
  cooperative queue. Current strongest movers get priority. Every mapped symbol
  is still queued, subject to availability and Kite API permissions.
- In-memory chart history is limited to 2 recently viewed symbols (configurable
  1–5). The 5m/15m/30m/1h chart requests a fresh 5m backfill on click and then
  caches it for 10 minutes while the stock remains in the hot chart set.
- After a new valid NSE-session tick, intraday tick counters, cumulative-volume
  deltas, and futures-OI reference reset; older completed days roll into the
  updated 20-day baseline. The app does *not* need a manual reset.

## Limits you cannot remove on Free

- Render Free provides **512 MB RAM, 0.1 CPU**. Large initial Kite history
  requests can still be slow, throttled, or fail under resource pressure.
- Render Free may **spin down after 15 minutes without incoming traffic**.
  Its outbound Zerodha WebSocket connection is not a guarantee of always-on
  processing. Cold starts interrupt streaming and history loading.
- The Free local filesystem is **ephemeral**. Compressed feature caches are
  retained only for the life of the current instance. After sleep/redeploy,
  Kite historical baselines must warm up again. Free instances **cannot**
  attach persistent disks.
- Kite access tokens expire; manually refresh them via Zerodha's supported
  login process and update the secret on Render each trading day.
- No Render Free setting makes a 24/7 reliable scanner. Do not depend on this
  for unattended trading alerts or execution.
- The web dashboard is **not password-protected**; do not publish it widely
  or redistribute licensed Kite data. Keep the GitHub repository private.

## Diagnostics

Open `/api/health` and look at `mode`, `feedStatus`, `lastQuoteAt`,
`marketStatus`, `historyLoaded`, `historyTarget`, `historyLoading`,
`historyFailed`, and `historyError` (when present on `/api/state`).

`feedStatus=connected` + `marketStatus=CLOSED` after market close is expected.
A stalled quote timestamp during trading hours, authorization error, or a
zero `historyLoaded` after several minutes should be investigated in Render Logs.

The service NEVER sends orders. `MARKET_MODE=kite` does not fall back to
synthetic data on credential failure.
