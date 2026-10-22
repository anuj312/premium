# Top 6 Score Cards — Release Notes

Every bullish and bearish Top 6 card now displays **MOM 79** and **OB 70 WAIT**
style compact scores (illustrative), plus the momentum state. These values are
computed by the unchanged historical/live rating engine; OB analysis is shared
with matching Top 3 cards rather than independently recalculated.

### Behaviour

- `MOM` is the existing four-window momentum score (5m, 15m, 30m, 1h).
- `OB` is the existing candle-derived order-block quality, with WAIT/READY/INVALID.
- Scores remain plain numbers, not `79/100`. Unavailable values are `—`.
- `ACTIVE`, `SIDEWAYS`, `FADING`, `WARMUP`, `STALE` and `DEMO` statuses
  are visible to distinguish candidates from confirmed setups.
- If a stock is simultaneously in Top 6 and Top 3, the score and OB status
  displayed in both locations are identical for that update.
- Read-only. No trades placed and no actual aggressor delta is inferred.
- OB results are cached for up to 25 seconds to keep Render Free efficient.

### Deploy

Replace the source files in your private repository, preserving your private
`MARKET_MODE`, `KITE_API_KEY`, and `KITE_ACCESS_TOKEN` secrets in Render.
Redeploy. No new variables or extra Kite subscriptions are required.

### Limitations

Visual browser screenshot capture was unavailable in the build environment.
Tests covered the generated payload and integration; actual Kite data requires
your own valid credentials and live exchange data.
