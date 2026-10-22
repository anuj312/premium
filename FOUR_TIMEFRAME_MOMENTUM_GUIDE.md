# OrderFlow Studio — Four Rolling Momentum Windows

This release extends the **live candidate ranking and momentum score** to **5m + 15m + 30m + 1h**. The pre-existing chart timeframe buttons are unchanged.

## What is compared

At time `t` within a trading day, for each equity with a live Kite minute tape:

- **5m** = `(price(t) / price(t - 5 minutes) - 1) × 100`
- **15m** = `(price(t) / price(t - 15 minutes) - 1) × 100`
- **30m** = `(price(t) / price(t - 30 minutes) - 1) × 100`
- **1h** = `(price(t) / price(t - 60 minutes) - 1) × 100`

Each reference must be from the **same session** and at the requested minute or within 1 minute earlier. Missing or stale quotes never turn into fictional numbers or previous-session lookbacks. The tape keeps at most **68 sampled minutes per stock**, including all 198 symbols, to remain feasible on Render Free. On a process restart the tape starts anew.

## Momentum and ranking

- **5m and 15m movement** must exceed ADR20-adjusted minimum thresholds in the bullish/bearish direction to become `ACTIVE`.
- **30m and 1h windows**, once available, must agree with the direction. A strong opposite move becomes `FADING`; weaker disagreement becomes `SIDEWAYS` (reported as `CONSOLIDATING` to the API).
- **Historical RVOL ≥ 1.0×** is required for `pickEligible`; missing RVOL is not silently accepted.
- The adaptive score prioritizes recent movement/volume, not a morning percent change; the session-to-date move retains only a bounded eight-point tie-breaker.
- **Top 6 each side → up to three ranked candidates each side** stays in place, whether or not candidates qualify as `ACTIVE`. Unconfirmed cards are marked WATCH, SIDEWAYS, FADING or WARMUP; they are not trade-entry signals.

### Momentum display score (0–100)

| Source | Maximum points |
| --- | ---: |
| Rolling 5m | 25 |
| Rolling 15m | 20 |
| Rolling 30m | 15 |
| Rolling 1h | 10 |
| Time-matched 20-session RVOL | 15 |
| Recent volume acceleration | 5 |
| Directional VWAP confirmation | 7 |
| 20-session breakout | 3 |
| **Total** | **100** |

Ratings continue to appear as a **plain number**, e.g. `79`, not `79/100`. A score is neither a probability of winning nor an order signal. Unobserved windows contribute **zero** rather than being extrapolated. Sideways, fading and unconfirmed scores retain safeguards/caps.

## Session warmup and restart

- ~09:20: 5m reference can become available.
- ~09:30: 15m available; early picks may qualify using 2/4 windows if RVOL is ready.
- ~09:45: 30m added.
- ~10:15: 1h added.

Times assume the server was already receiving market quotes from **09:15 IST** and today is an NSE trading session. Late Render starts or sleep/restart push availability back. The scanner does not wait until 10:15 to display Top 3 candidates.

## Frontend

The Top 3 card footer now displays `OPEN`, `RVOL`, `5m`, `15m`, `30m`, and `1h` in a compact two-row grid; missing windows display `—`. Expanded stock details show the four rolling changes and `N / 4` windows available. This update does **not** alter the 5m/15m/30m/1h candlestick chart controls, dark/light themes, or Kite WebSocket feeds.

## Verification

From the folder containing `server.py` run:

```bash
python -m unittest discover -p 'test_*.py' -q
node --check static/app.js
```

Mocked tests cover 2/4 early warmup, 3/4 at 30m, 4/4 after 60m, bullish/bearish paths, hourly disagreement, an old spike going sideways, and session rollover. A live Kite session must still be verified with your own access token.

## Render Free deployment

Upload updated source to your **private** GitHub repository and redeploy your existing Render web service. Keep `MARKET_MODE=kite`, `KITE_API_KEY`, `KITE_ACCESS_TOKEN`, and existing memory limits in Render environment variables. **No new variables are required.** Render Free sleep and daily Zerodha token expiry can interrupt or reset the current-session tape.
