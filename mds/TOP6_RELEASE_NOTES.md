# OrderFlow Studio — Top 6 to Top 3 Edition

This release changes the scanner's visible daily-momentum shortlists from **10 stocks per direction** to **6 stocks per direction**.

- **Bullish Momentum Leaders:** Top 6 by existing 20-day-adjusted daily-strength rank.
- **Bearish Momentum Leaders:** Top 6 by existing daily-strength rank.
- **Top 3 picks:** Taken **only** from the corresponding six leaders. In live Kite mode, up to 3 real candidates always display on each side, with ACTIVE + RVOL-qualified names prioritized; unconfirmed slots are visibly WATCH / SIDEWAYS / FADING / WARMUP.
- **Layout:** Two columns × three rows on each side. Larger tiles use the freed space while preserving the dense horizontal estimated candle-volume profiles.
- **Unchanged:** Zerodha Kite Connect, 198 symbols, 20-day history, market-session resets, Momentum score, OB Quality score, order-block intelligence, 5m/15m/30m/1h chart, zoom/drag/pinch, premium chart V3, dark/light theme, mobile responsiveness and Render Free optimization.

## Deployment

Copy updated files into your existing private GitHub repository and redeploy on Render. No new environment variables are needed. Keep your existing private `.env` file and Kite credentials on Render, not in source control.

## Verification

Run `python -m unittest discover -p 'test_*.py'` (or `pytest -q`). The backend API in demo mode should return `len(bullish) == 6`, `len(bearish) == 6`, and up to three picks per side. During market hours, fewer than three qualifying picks is intentional.

**Note:** Bundled image files named `preview-*` are visual references from the previous ten-leader edition. Their stock counts may be outdated; the running application code displays six per side. Live Kite rendering must be verified with a valid session.
