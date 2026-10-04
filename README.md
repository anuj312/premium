# Pulse Premium

Version: `premium-2.4-session-pressure`.

Read [README-live.md](README-live.md) for installation, deployment, liquidity
definitions, Setup Building, experimental booster rules, Session Buy / Sell
Pressure, testing and limitations. Session pressure accumulates every completed
5m candle from 09:15, first displaying at 09:20, and ranks by cumulative percentage.
Current resting depth and latest 5m pressure are shown separately. These are
volume-weighted close-location estimates; actual executed buy/sell volume is
unavailable. Opt-in in-app alerts are observation only, not trade entries.

Your existing approved-number allowlist is included privately in `numbers.txt`.
Git intentionally ignores it. On Render, add it under Environment > Secret Files
as `numbers.txt` and set `ACCESS_NUMBERS_FILE=/etc/secrets/numbers.txt`.
See the private access-list instructions in README-live.md before deploying.
No Kite keys, Git history, market caches, Mac metadata or virtual environment
are included in this clean package.
