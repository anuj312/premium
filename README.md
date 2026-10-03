# Pulse Premium

Read [README-live.md](README-live.md) for installation, deployment, liquidity
definitions, experimental booster rules, testing and limitations.

Your existing approved-number allowlist is included privately in `numbers.txt`.
Git intentionally ignores it. On Render, add it under Environment > Secret Files
as `numbers.txt` and set `ACCESS_NUMBERS_FILE=/etc/secrets/numbers.txt`.
See the private access-list instructions in README-live.md before deploying.
No Kite keys, Git history, market caches, Mac metadata or virtual environment
are included in this clean package.
