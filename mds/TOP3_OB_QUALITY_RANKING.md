# Top 3: session-stable OB Quality picks (updated)

User goal: do not let the top/right-side bullish and bearish pick cards continuously swap symbols, or replace a scored pick with a random stock whose OB Quality is unavailable.

- Top 6 bullish/bearish lists **still refresh by adaptive intraday momentum**.
- Top 3 bullish and bearish are **separate session-locked panels**. The first valid scored candidate on either side is pinned. When three valid OB Quality scores are available, slots are filled highest-first among newly unpinned stocks. If all three are available initially, the best three by OB Quality are chosen together. Once shown, **a symbol and its position remain stable for the rest of that trading session**. This is a **pinned watchlist**, not a continuously re-sorted leaderboard.
- A pinned name stays visible even if its MOM falls, it leaves Top 6, it becomes bearish instead of bullish, or its OB pattern loses its current quality score. The backend retrieves that name from the tracked universe so price, momentum and validated OB quality can update even when it is no longer in the Top 6.
- If a pinned name switches directional side, label it **SIDE CHANGED** and never mark the old directional pick confirmed.
- **Never introduce a new stock with a missing OB score.** During initial warmup, unfilled slots explicitly say they are waiting for a valid completed-candle order-block score. If an already pinned name loses its valid OB pattern, show the current OB `—` with `WAIT` rather than a fabricated number, while keeping the ticker.
- The Top 3 side panel **ignores sector/watchlist filters** so those user actions cannot make a pinned ticker disappear. Filters continue to work on the other panels.
- At the next market session, the pin list resets and new candidates are selected. Server restart also resets it unless the process retains the state; Render redeployments cannot guarantee persistence.
- No trades are placed; `READY` / `WAIT` / `INVALID` and `ACTIVE` / `WATCH` / `SIDE CHANGED` represent different analysis states, not entry orders or expected performance.

Implementation: `server.py` maintains `top3_locked`, `top3_lock_date`, and fallback last-known display data. Browser `static/app.js` renders backend picks directly for all filters. The backend caches closed-candle OB evaluations for pinned names; no additional Kite requests are made by this change.
