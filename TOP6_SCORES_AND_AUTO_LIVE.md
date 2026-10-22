# Top 6 header scores + chart AUTO follow

## Changes from the preceding Top 6 Momentum/OB edition

- **No `01`–`06` badges on Top 6 stock tiles.** Each tile now displays its current **MOM** (momentum strength) and **OB** (order-block quality) numeric ratings in the **top-right corner**, replacing rank numbers. No `/100` suffix is displayed. Color/tooltip and an OB state label (`WAIT`, `READY`, or `INVALID`) remain. The backend still retains internal ranking for selection and full chart details.
- On selecting a stock or changing the chart timeframe, the expanded chart starts with **automatic latest-candle following turned on**. The toolbar displays `● AUTO` and the bottom hint says `AUTO-FOLLOW`.
- Every 15 seconds **while Kite mode is connected and the chart is open**, the existing chart refresh retrieves the latest available candles and the viewport keeps the latest candle at the right edge. An open/incomplete candle can update without increasing the candle count. Zooming while AUTO is on retains that live-edge position.
- **Drag/pan left or use the keyboard's left arrow** to view history. The window stops auto-following and shows `HISTORY VIEW`; the chart stays at the historical position during subsequent refreshes.
- Press **`↗ LIVE`** (or the `End` key) to resume following the latest candles. `Fit All` also returns to auto-follow mode. Panning forward to the right edge re-enables auto-follow automatically.
- The chart's `AUTO` control indicates the **viewport behavior**; it does **not** prove that Kite quotes are fresh. The top-bar market/connection status still determines feed freshness.
- This is a frontend-only change. It does not alter Kite API credentials, four-timeframe momentum formulas, order-block calculations, the Top 6 → Top 3 selection, or Render Free optimizations.

## Deploy

Unzip and replace the previous project files in your private GitHub repository. Keep your Render `.env`-equivalent credentials in Render's Environment settings, not in GitHub. Redeploy and force-refresh the dashboard once. No extra environment variables are required.

## Tests

The `test_chart_autofollow.py` suite executes chart-window logic in Node's JavaScript VM and checks that fresh candles follow automatically, historical pan remains anchored during updates, and the LIVE button resumes follow mode. The existing Python backend suite remains included. Tests run with mock/demo data; a real Kite stream must be verified on your own deployment.
