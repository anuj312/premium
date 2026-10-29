# Top 3: momentum selection, OB Score display order

On each streaming snapshot:

1. The usual adaptive intraday ranking selects the **Top 6** bullish and bearish leader pools.
2. Within each Top 6, select the **three highest `momentumScore100`** (higher first; missing/stale scores last; adaptive rank then daily score break ties). This selection never favors different sectors or `pickEligible`.
3. Calculate/reuse the existing closed-candle `obQualityScore` for the Top 6 leaders.
4. **Sort those selected three** by `obQualityScore` descending. Missing OB scores go last; momentum breaks OB ties. Bullish and bearish are ranked independently.
5. The browser's sector/watchlist-filtered Top 3 follows the same two-stage selection and display order.

OB WAIT/INVALID states and momentum ACTIVE/WATCH/WARMUP labels are preserved; a high score is not trade confirmation. Live feed and broker integration are unchanged.
