# KiteNeonFlow V14 — One-Time Seeding + Live 5-Minute KiteTicker Candles

A **read-only** dark-neon NSE trading dashboard with sector breadth, **6 bullish + 6 bearish Pro Score leaders**, order-block candles, EMA50, and nearest-expiry FUTSTK OI confirmation. The displayed candles are **NSE CASH**, not futures candles. The neon flow lines represent relative price movement, **not trade or institutional order flows**.

## What V14 changes

In `MODE=live` with `FEED=ticks`, the app now:

1. **Starts the Render website immediately**. Kite instruments, first quotes, and the WebSocket initialize asynchronously. `/healthz` responds while the feed seeds.
2. **Seeds once per Indian trading date**: reads up to 48 calendar days of historical 5-minute candles **once per NSE instrument** (subject to missing history), deriving a time-matched average cumulative-volume curve from 20 completed sessions, historical EMA warmup, and any 5-minute bars completed today. A separate daily-history pass computes the 20-day average absolute daily price move.
3. **Processes KiteTicker FULL-mode cash ticks** continuously. It uses `last_price` for OHLC and **differences in cumulative `volume_traded`** for bar volume, anchored to NSE 09:15–15:30 IST session boundaries.
4. **Closes each 5-minute candle only after its ending time**. Available valid scores are recomputed from cached history + closed live candles. The dashboard refreshes every `POLL_SECONDS` (default 2 seconds) without HTTP history requests each five minutes.
5. **Repairs detected gaps** if KiteTicker disconnects, a candle begins after a mid-session startup, or a bucket goes missing. These recovery requests are sequential, paced, and throttled per symbol to avoid repeated bursts. If a missing candle cannot be verified from Kite, the score is held instead of filled with fake data.
6. The **5m inspector chart reads the seeded candle cache**; other user-selected chart intervals (`1m`, `3m`, `15m`, `30m`, `1D`) still query Kite historical data **on demand**.

This one-time seeding behavior applies to **live ticker mode**. `FEED=rest` retains the older history-refresh behavior, and `MODE=demo` uses simulated prices/candles. On the next IST session it seeds again; a Render restart (in-memory state is lost) also requires fresh seeding.

### Market-data precision

- **5-minute streaming volumes are estimates from changes in Kite's session-cumulative volume**. If a trade falls between the final pre-boundary tick and the first post-boundary tick, its volume can be attributed to the newer bar. It is not exchange-certified historical OHLCV. Gap recovery uses Kite's reported historical candle volume.
- The initial partial candle after a **mid-session process start** is not assigned an invented opening volume. Until broker history can supply it, the affected 5m score may be delayed.
- Instruments with no trades in an interval may have no candle; there is no interpolation of zero-volume bars. Some stocks can remain unscored if data is unavailable.
- Scores need at least **12 completed 5m candles**, the 20-session volume curve, and the 20-day price-movement reference. The first possible score under a normal 09:15 opening is **10:15 IST**.
- **Futures OI points** use the same FUTSTK contract's *since-connection* futures-price and OI changes; they're not a previous-day OI comparison and reset when the application reconnects or starts a new session.
- NSE market holidays are not proactively fetched. The script checks ordinary NSE weekday/session hours and waits if the exchange is closed. Live API authentication and Kite access-token renewal remain your responsibility.

## Pro Momentum Score: 0–100

| Component | Points |
|---|---:|
| 20-day normalized price momentum | 20 |
| Time-matched relative volume | 20 |
| 5-minute trend efficiency and 15/30/60m persistence | 20 |
| VWAP | 10 |
| EMA50 | 10 |
| Nearest-expiry futures price + OI confirmation | 10 |
| Sector direction | 5 |
| Breakout / breakdown | 5 |

Ranks are **descending for both bullish and bearish** because the score measures the quality of momentum in its own direction. All scores are heuristic and not backtested. Missing historical inputs display `—`, not a fabricated score. **Stock Matrix shows 6 bullish + 6 bearish stocks** and no internal matrix scrollbar. The signal inspector hides the bulky score breakdown and focuses on the chart.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Initially MODE=demo. Open .env to configure live mode later.
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open http://127.0.0.1:8000. To run live, add your Kite API key/secret to `.env`, run `python auth.py` interactively on your Mac to save `KITE_ACCESS_TOKEN`, then restart. Never push `.env` to GitHub.

## Render deployment

Build: `pip install -r requirements.txt`

Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`

Health check: `/healthz`

Render environment:

```ini
MODE=live
FEED=ticks
POLL_SECONDS=2
KITE_API_KEY=your_api_key
KITE_ACCESS_TOKEN=your_fresh_access_token
```

Your Kite API secret is required for local token generation, not ongoing Render streaming. Kite access tokens generally expire the following morning, so update them as required. Use an always-on Render instance: sleeping or redeploying loses the in-memory candle stream. **Do not run more than one Uvicorn worker or independent live app instance** without a shared state architecture; extra workers would open duplicate WebSockets and produce diverging market caches.

### Verify that historical polling has stopped

Open `/api/state` (on a secured instance). After initial seeding, inspect:

- `meta.proscore.status` → `ready` (or `unavailable` if historical inputs are missing)
- `meta.proscore.source` → `kite_websocket`
- `meta.stream_5m.seeded` → number of instruments seeded this session
- `meta.stream_5m.closed_tick_candles` → grows as streaming 5m bars close
- `meta.stream_5m.last_closed_at` → Unix timestamp of last completed streamed bar
- `meta.stream_5m.pending_recovery` → instruments awaiting gap repair
- `meta.stream_5m.recovery_fetches` → count of broker history gap repairs
- `meta.received_ticks` → count of incoming live ticker ticks

If an instrument isn't scored, check its `pro_reason` and the missing-history progress fields. V14 never falls back from failed **live** Kite data to fake demo values.

### Files

- `main.py` — FastAPI, background one-time seed, scored-candle loop, recovery, and cached 5m chart endpoint
- `stream5m.py` — thread-safe 5m candle aggregation, volume deltas, recovery flags, day reset
- `market.py` — KiteTicker callback adapters, OI and cash snapshots, scores
- `proscore.py` / `momentum20.py` — score features and 20-day historical references
- `charting.py` — OHLC normalization and heuristic order-block candidates
- `sectors.py` — supplied stock universe and sector definitions
- `static/` — neon dashboard and chart
- `tests/` — backend and deterministic tick-stream tests

To run tests: `PYTHONPATH=. python -m pytest -q` (install pytest if necessary).

**Privacy / regulatory note:** The frontend currently has no user authentication. Do not expose Kite market data publicly without suitable login protection and permissions. Use a private deployment to start. This is not trading advice or an order execution system.
