# OrderFlow Studio — Signature Chart Detail V3

The expanded stock chart has been visually redesigned without modifying the Kite Connect feed, Top 10/Top 3 selection, 20-day intelligence, scoring formulas, or order-block calculations.

## New visual system

- Premium stock header with better symbol hierarchy, refined actions and focused price emphasis.
- Four distinct statistical cards with subtle, color-coded edge accents.
- Market-structure chart header, honest demo/Kite/external data-source indicator, grouped timeframe control, zoom and navigation controls.
- Premium chart/volume canvas, legible axes, modern interaction legend, and themed hover tooltip.
- Compact, color-aware OB Quality and WAIT/READY/INVALID status strip.
- Matching light and dark treatments, responsive mobile design, and motion accessibility.

The four timeframes (5m, 15m, 30m, 1h), mouse-wheel zoom, drag panning, keyboard navigation, and touch pinch remain active. On narrow phones, chart aspect ratio is tuned to use the space more effectively.

## Files changed

- `static/chart-premium-v3.css` — the new scope-isolated visual layer.
- `static/index.html` — loads the visual layer last.
- `static/app.js` — adds semantic classes and a true data-source badge to the stock details; improves mobile SVG aspect ratio only.

Other Python market-data and scanner files remain unchanged from the prior Interactive Chart ZIP.

## Live deployment

Copy the project files into your existing **private** GitHub repo and redeploy Render. Preserve your existing Render environment variables, including `MARKET_MODE=kite`, `KITE_API_KEY`, and `KITE_ACCESS_TOKEN` in Render's environment dashboard, not GitHub.

No new environment variables, browser libraries, paid charting packages, or new API calls are introduced by this redesign.

The screenshots are created in demo mode using synthetic market data, not a Kite connection.
