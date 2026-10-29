# Stock detail layout — chart-first header

This version starts from `premium_top3_stable_dynamic_ob.zip`. The existing stable-dynamic Top 3 bullish/bearish OB ranking code and tests are preserved.

## Updated visual layout

- Compact navigation and close/watchlist buttons.
- Beside the stock name and price on desktop: **FUTURES OI Δ**, **PRICE ACTION**, **RELATIVE VOLUME**, and **OB QUALITY** with WAIT/READY/INVALID status.
- A compact timeframe toolbar with 5m / 15m / 30m / 1h, AUTO follow, TradingView link, + / − zoom, and Fit.
- A larger full-width price-action and order-block chart directly underneath; no separate bulky metrics row, chart-toolbar row, or bottom OB score bar.
- Mobile: metrics remain visible in four short tiles, with timeframe/AUTO/TV above the now vertically expanded chart. Zoom/fit buttons hide on very narrow phones, where touch zoom/pan remain available.

## Data source and safeguards

No trading orders, scanner ranking formula, broker credentials, live-data collection, signal heuristics, or OB score calculation were changed. A numeric quality score may still show WAIT: the badge is retained and no confirmation status is fabricated. The TradingView link retains the original NSE 5-minute destination. Live trading values require a configured Kite data feed; demo values remain labeled simulated.

## Verification

- `node --check static/app.js`
- `python -m unittest discover -s mds -p 'test_*.py' -q`
- Visual previews tested at 1920×1080 and 390×844 using the demo-symbol details.
