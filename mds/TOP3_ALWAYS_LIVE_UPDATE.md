> **Important: later behavior change (session-pinned OB picks).**
> This file describes an older dynamic-ranking release. As of the stable Top 3
> change, the Top 3 waits for valid OB Quality scores and pins scored symbols
> for the session. A new symbol with missing OB Quality is not substituted.
> See `TOP3_OB_QUALITY_RANKING.md`. The Top 6 remains dynamic.

# Release: Top 6 → Always Visible Top 3 + Adaptive Momentum

## Reported problem and correction

Previously live Top 3 stayed empty (`0/3`) because both the API and browser
required a stock to pass fresh 5m/15m momentum **and** a completed RVOL(20)
baseline. An otherwise healthy Kite connection could therefore show no picks.
A large spike at the open could also stay highly ranked using full-day change.

The new behavior:

- The best **up to three actual quoted stocks in each direction** appear,
  including during the 15-minute warmup and while 20D history loads.
- Confirmation remains separate: `ACTIVE`, `WATCH · RVOL`, `SIDEWAYS`,
  `FADING`, `WARMUP`, `STALE` and `FEED WAIT` do **not** mean the same thing.
- Newly advancing stocks can move above an early-spiking stock because rank is
  based predominantly on current directional 5m/15m activity, time-matched
  RVOL, VWAP and recent volume. Session-open-to-current return is capped at
  **8 ranking points**; full-day change is no longer the main ranking metric.
- The displayed plain-number `MOMENTUM` score changes with activity. Sideways
  candidates are capped at 42, fading at 25, and first-15-minute warmup at 35.
  Stale stocks have no current verified number; do not interpret it as 0.
- The Top 6 lists and Top 3 candidates are refreshed together by SSE; the
  expanded stock's SCAN RANK matches the live dashboard rank.
- `OB Quality` and WAIT/READY/INVALID remain independent. A candidate in a
  Top 3 slot is not an entry recommendation. No orders are placed.

### Data limitations

A direction with fewer than 3 quoted symbols cannot display 3 real prices.
An expired Kite token, disconnected WebSocket, or sleeping Render Free instance
cannot generate true live quotes: cards may show `FEED WAIT` or `STALE`.
The 20-day baselines remain rate-limited and can take time after a Render Free
reboot. No fresh or missing observations are synthesized.

## Render Free upgrade

Replace the existing private GitHub repository files, commit and redeploy on
Render. Preserve private `MARKET_MODE=kite`, `KITE_API_KEY`, and
`KITE_ACCESS_TOKEN` secrets in Render environment settings. No new variables
or paid chart libraries are required.

Run tests locally: `python -m unittest discover -p 'test_*.py' -q`.
