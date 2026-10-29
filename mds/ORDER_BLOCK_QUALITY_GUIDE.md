# OrderFlow Studio · Order Block Quality and Setup States

This edition adds a **separate 0–100 order-block quality rating** to the existing 0–100 fresh momentum rating. **The user interface displays plain numbers**, such as `79`, not `79/100`. The API retains `momentumScore100` and `obQualityScore` as explicit field names for downstream integrations.

## Where it appears

- **Top 3 Bullish and Top 3 Bearish:** each pick shows two tiny badges: `79 · MOMENTUM` and `82 · OB · WAIT` (example only).
- **Expanded chart:** compact `OB QUALITY [score] · WAIT / READY / INVALID` status strip, zone range, and BOS/FVG/sweep observations. The chart highlights a **validated candidate zone** in addition to the existing less-strict candidate OB rectangles. When identified, an FVG shaded region, BOS line, and sweep marker appear on the chart.
- **5m, 15m, 30m, 1h:** OB quality is recalculated using the selected timeframe's completed bars. **It can be N/A on higher timeframes until enough candles form.** The 15-minute trend provides higher-timeframe confirmation. This does not represent authenticated executed-order flow.

## What it detects

1. **Displacement and break of structure (BOS):** An opposing candle followed within the next 1–3 completed bars by a directional close beyond the preceding local high/low and an impulse body at least 1.15× the local median candle range.
2. **Order-block zone:** bullish = low to top of last bearish candle body; bearish = bottom of last bullish body to its high. This is a candle-derived estimate, not observed institutional orders.
3. **FVG:** A strict 3-candle imbalance near the BOS/displacement, e.g. a bullish third bar's low above the first bar's high; shown only if present.
4. **Liquidity sweep proxy:** A completed wick through a recent local high/low **plus a close back inside**. This is a *price-action sweep hypothesis*, not proof that resting stop orders were filled.
5. **Impulse volume confirmation:** Impulse-candle volume relative to preceding bars; fallback to 20-day RVOL only if eligible and supplied. Missing data provides zero points, not a fake value.
6. **15-minute higher-timeframe direction** and **current session VWAP alignment**.
7. **Retest and bullish/bearish rejection**, measured on completed candles only. One close beyond the zone invalidates that candidate. Retests expire rapidly rather than remaining READY all day.

## How the 0–100 OB score works

| Factor | Maximum |
| --- | ---: |
| BOS + displacement | 25 |
| Order-block freshness | 15 |
| 15-minute directional confirmation | 15 |
| Impulse volume | 15 |
| FVG | 10 |
| Recent retest + rejection | 10 |
| Liquidity sweep price proxy | 6 |
| VWAP confirmation | 4 |
| **Total** | **100** |

If the latest OB was invalidated, its rating is capped at 25. If no qualifying completed-candle structure exists, show `—` (null in API), not a fabricated quality number. The raw score is a heuristic research scale, **not a 79% likelihood of profit**.

## Setup state definitions

- **WAIT** — no qualifying OB yet, retest is pending, higher-timeframe confirmation missing, closed market/stale feed, old retest or insufficient history.
- **READY** — validated zone, recent completed-bar retest and directional rejection, 15m trend alignment, OB quality >=60, and a fresh LIVE market feed for non-demo data.
- **INVALID** — a completed candle closes through the OB invalidation threshold. The invalidated candidate is shown with a capped score.

`READY` is **not an order-placement command**, a guaranteed trade setup, or a recommendation to buy/sell. The model does not check executable bid-ask spread or slippage, and no trades are placed automatically. Backtest and review false positives before deciding whether to trade.

## Delta disclaimer

Zerodha Kite Connect quote/depth messages do **not** directly classify each executed transaction as aggressive-buy or aggressive-sell. The system therefore does **not** manufacture true Delta/CVD. `obDeltaSource` is explicitly `UNAVAILABLE — no aggressor trade classification`. Order-book depth (pending orders) is not trade delta.

## Data and performance

- No extra Kite API requests for this analysis: it runs on existing cached candle histories.
- Full-scanner OB analysis is restricted to the twelve visible Top 6 leaders; the Top 3 reuses those results, while selected-chart analysis runs when opening or refreshing that stock.
- Current incomplete bars are excluded from OB confirmations. Trade-day boundaries are handled separately; market hours and quote freshness are checked before live READY.
- Compact indicator cache, Render Free throttling, and a two-chart memory cap remain unchanged.
- Simulated/demo candidates are always labeled **SIMULATED**.

## Verify

```bash
python -m unittest discover -p 'test_*.py' -v
node --check static/app.js
```

Deploy on Render by updating your existing private GitHub repository, keeping the same service start command and existing server-side `MARKET_MODE`, `KITE_API_KEY`, and `KITE_ACCESS_TOKEN` variables. No new environment variable is needed.
