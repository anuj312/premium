# Current 5m + 15m + 30m + 1h Momentum Ratings

Refer to [FOUR_TIMEFRAME_MOMENTUM_GUIDE.md](FOUR_TIMEFRAME_MOMENTUM_GUIDE.md) for current allocations and logic.

# OrderFlow Studio — 0–100 Momentum Strength Ratings

The Top 3 bullish and bearish pick cards now display a large **momentum-strength score** (`e.g. 79`, without /100) instead of their old `#1 PICK`, `#2 PICK`, and `#3 PICK` badges. The symbol, daily % move, RVOL and recent 5m/15m changes remain visible. The score is also displayed in the expanded stock details.

**This score is not a win probability, prediction of return, or trade recommendation.** It is a normalized description of recent directional movement and volume participation.

## Previous 5m/15m scoring (superseded by four-window release)

| Factor | Max points | How it works |
|---|---:|---|
| 5-minute fresh movement | 30 | Directional 5m price movement divided by three times the existing ADR20-adjusted 5m activation threshold, capped at 100% of the component |
| 15-minute fresh movement | 25 | Directional 15m price movement divided by three times the ADR20-adjusted 15m activation threshold, capped at 100% |
| 20-session time-matched RVOL | 20 | Today's cumulative volume divided by the average cumulative volume at the same time across valid previous sessions; full points at 4× RVOL |
| Recent 5-minute volume acceleration | 10 | Last 5 minutes' volume / previous 5 minutes' volume; full points at 3× acceleration |
| VWAP alignment | 10 | Above VWAP for bullish, below VWAP for bearish; unavailable VWAP earns no bonus |
| 20-day directional breakout | 5 | Above prior 20-day high for bullish, below prior 20-day low for bearish |

`momentumScore100` is rounded to the nearest integer and capped at 100. The API also provides `momentumScoreBreakdown` and `momentumScoreBasis` for transparency. A complete baseline is `LIVE_20D`; if only the minimum valid historical volume sessions are present, the basis says `LIVE_PARTIAL_HISTORY`. No future candles or order-book depth are used.

## Eligibility and Top 3 selection

- Top 6 leaders on each side continue to use the adaptive intraday ranking (fresh momentum, RVOL, VWAP, capped session move).
- To be labeled **ACTIVE**, a candidate requires positive directional 5m/15m movement, **RVOL ≥ 1.0**, and a fresh Kite feed; every Top 3 slot displays a real candidate even when these conditions are not satisfied.
- Within each current Top 6, the three highest displayed `momentumScore100` candidates are selected, without sector diversification or eligibility priority. Those three are shown in descending `obQualityScore` order; missing OB scores go last. Momentum is a strength rating, not a win probability.
- Sideways, fading, stale, or warming-up stocks may appear as **unconfirmed candidates** with bounded/provisional scores, never as verified ACTIVE entry signals. Old opening spikes are downweighted when fresh 5m/15m momentum fades.
- A server restart on Render Free can restart 15-minute freshness warm-up. Daily historical reference loading also takes time.

## Demo mode

`MARKET_MODE=demo` shows illustrative 0–100 ratings with the label **SIMULATED**. They are not verified market strength scores; demo mode has no actual live Kite history. In real Kite mode, missing/stale momentum scores rank last, but the actual quoted candidates can still appear with an unavailable rating.

## Deployment

No new environment variables. Update your existing Render Free GitHub repository with these files, preserving your secret `.env` / Render environment settings, and redeploy using the existing `uvicorn server:app --host 0.0.0.0 --port $PORT` command. Keep one worker. All previous themes, 20-day calculations, animated wires, mobile layout, chart timeframes and Kite WebSocket behavior remain in place.

## Tests

Run `python -m unittest discover -p 'test_*.py' -q` from the `orderflow-studio` folder. Added `test_momentum_rating.py` for the rating boundaries, bearish direction, missing information, fake/demo labeling, and early spike followed by sideways behavior.
