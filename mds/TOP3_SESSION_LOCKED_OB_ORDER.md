# Top 3: fixed session membership, live OB Quality display order

## Behavior

- Each bullish and bearish side locks up to three symbols for the trading session once valid OB Quality scores become available, following existing admission logic.
- The **same symbols remain pinned** through live updates, sector/watchlist filter changes, OB-quality changes, and temporary OB-score unavailability. The session lock and initial admissions are unchanged.
- The **card order** (not the membership) is refreshed by the **current numeric OB Quality Score, highest to lowest**, on both bullish and bearish sides.
- A pinned stock with no current numeric OB score stays visible at the bottom as an OB WAIT/— card; no unscored replacement is added.
- For equal OB scores, the original pin order is retained to avoid unnecessary movement on ties.
- On a new session or server restart, existing session-lock reset rules still apply.
- The dynamic Top 6 leader list is unaffected.

### Example

Pinned names in original selection order:

- DELHIVERY — OB 70
- CAMS — OB 82
- TATAPOWER — OB 80

Displayed positions: CAMS (82), TATAPOWER (80), DELHIVERY (70).

## Validation

Run `python -m unittest discover -s mds -p 'test_*.py' -q` from the `premium` folder.
