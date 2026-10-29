# Top 3: OB Quality selection AND display order

For each live snapshot:

1. The usual adaptive intraday momentum calculation builds **Top 6 bullish** and **Top 6 bearish** leader pools. The Top 6 rankings are unchanged.
2. Calculate/reuse the closed-candle `obQualityScore` for all twelve leaders (cached; no new broker requests).
3. **Select the three highest OB Quality scores out of each side's Top 6**. Do not first select the three highest momentum stocks; do not favor sector diversity or `pickEligible`.
4. Display both Top 3 lists by **OB Quality descending**. Ties use MOM, then adaptive rank. Missing OB Quality goes last. If fewer than three scored stocks exist, include unscored rows to keep up to three candidates visible.
5. Sector/watchlist-filtered lists also select the top three from the visible Top 6 based only on OB quality.

OB `READY` / `WAIT` / `INVALID` and momentum status `ACTIVE` / `WATCH` / `WARMUP` remain visible. An OB score is a heuristic assessment of a completed-candle pattern, **not a confirmed trade or win probability**.
