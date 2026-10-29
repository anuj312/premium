# Top 3 Bullish/Bearish — stable dynamic OB Quality ranking

## Selection

- Top 6 per side continues updating by momentum. From each side's **current Top 6**, select the highest **eligible** OB Quality candidates for any empty Top 3 slots. A numerical OB score by itself does not mean the zone is confirmed.
- READY qualifies if the current directional OB zone is valid, its OB direction matches the side, it has a finite quality score, and the feed is LIVE (or the app is in labelled demo mode).
- A WAIT candidate qualifies only as a **WATCH** candidate in LIVE mode if all of these hold: a current, non-invalidated matching OB zone exists; its numeric quality is at least 60; the fresh momentum engine marks `pickEligible=true`; and the reason is an active waiting-for-retest / waiting-for-confirmation case. WAIT caused by stale candles, expired zones, missing patterns, or missing momentum confirmation is excluded. INVALID is always excluded.
- For each side, at most three ticker symbols appear. The remaining slots are **visible placeholders** labelled `WAITING FOR ELIGIBLE OB` — not invented picks.

## Stable dynamic replacements

- A new eligible candidate must have a **strictly higher** OB Quality than the weakest of the three currently displayed eligible stocks. A current member with stale, wrong-side, invalid, or no longer eligible OB is treated as the weakest candidate to replace, **but stays visible until confirmed replacement**.
- A replacement occurs only after the same challenger stays ahead of the same incumbent across **three distinct market-data revision updates**. Repeated page loads, HTTP snapshots, and SSE subscriptions without a newer feed revision do **not** advance the counter. If the challenge breaks, the counter resets.
- Current members remain visible when they leave the moving Top 6, change side, or temporarily lose their score, until replaced by a confirmed candidate. On direction changes the card shows `SIDE CHANGED` and cannot be considered a confirmed pick.
- Cards are **always sorted by current OB Quality descending**. A missing score goes last; no old score is fabricated. Sector/watchlist filters do not alter these three-slot side panels.
- Selection state and pending challenges reset on the next exchange session or server restart. These are analysis/watchlist suggestions, not trades or success probabilities.

## Validation

From the `premium` folder, run:

```bash
python -m unittest discover -s mds -p 'test_*.py' -q
```
