# V14 Render quick deploy — cached KiteTicker candles

1. Put the **contents** of `KiteNeonFlow_V14_TickCandles/` at your GitHub repository root (or set Render's Root Directory to that folder). Do not upload `.env`.
2. Render Web Service → Python 3 → Build: `pip install -r requirements.txt`.
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`.
4. Health check: `/healthz`. Choose an always-on plan for live ticks.
5. First deploy `MODE=demo` to check website rendering. For real-time market feeds add `MODE=live`, `FEED=ticks`, `POLL_SECONDS=2`, `KITE_API_KEY`, and a valid `KITE_ACCESS_TOKEN` in Render → Environment and redeploy.
6. Open `/api/state`: `status=ok` means market quotes loaded. Meanwhile `meta.proscore.status=loading` indicates the **one-time** per-symbol 5m history seed is still processing. It may take several minutes. When complete, `meta.stream_5m.closed_tick_candles` counts new candles constructed from ticks.
7. The frontend stock-matrix banner changes from **ONE-TIME SEED n/198** to **TICK CANDLES · LAST CLOSE hh:mm IST**. Missing intervals may briefly show **GAP REPAIR**.

**Normal cadence:** Full 5m and 20D histories are fetched once each new IST trading day, not every five minutes. 5m scores thereafter come from cached closed ticker candles. REST history is still used on stream gaps/reconnect, an initial startup/restart seed, and user-requested chart intervals other than 5m. A 5m chart uses cache after seeding.

**Do not scale workers horizontally.** Each worker has its own KiteTicker subscription and in-memory market candles. Read README for gaps, volume estimates, access-token renewal, data permissions, and instrument expiry rollover limitations.
