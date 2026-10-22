# Top 6 header badge / Render UI cache fix

## Expected card appearance
Every Top 6 Bullish and Top 6 Bearish tile now shows **MOM [number]** and **OB [number]** in its upper-right corner, in place of the old 01–06 tile rank labels. A missing score is shown as `—`, never as a false calculated value. Header labels `06 / 6` remain because these are counts, not tile rank labels.

## Why it looked unchanged
The previous ZIP already included the MOM/OB card renderer. A Render deployment running an older commit, or an old browser/CDN asset, can still show the earlier `01–06` tiles. Updating CSS alone will not create scores if the browser continues to run older JavaScript.

## Improvements in this release
- Top 6 card UI contains no rank badge markup.
- The UI is marked `data-leader-card-ui="mom-ob-header-v2"` on the document root while the current JavaScript is running.
- The server calculates a **SHA-256 fingerprint** of `static/app.js` and `static/top6-scores.css`. Their URLs use this unique value, automatically changing whenever those assets change.
- Root `/` responses and the two critical frontend assets carry `Cache-Control: no-store` to prevent reusing outdated UI code after deployment.
- `/api/version` reports `uiBuild`, `top6CardHeader: "MOM + OB"`, `legacyRankBadges: false`, and `liveChartFollow: "AUTO"` without leaking credentials. The same `uiBuild` is also included in `/api/health`.
- Added runtime markup and HTTP caching regression tests.

## Deploy on Render Free
1. Extract this ZIP.
2. Commit the **contents of `orderflow-studio/`** to the root directory of the private GitHub repository used by Render, or set Render's Root Directory to `orderflow-studio`.
3. Confirm `static/app.js`, `static/index.html`, `static/top6-scores.css`, and `server.py` all changed in the commit.
4. Render Dashboard > your Web Service > **Manual Deploy > Deploy latest commit** (or allow auto-deploy if enabled).
5. Wait until the deployment is Live, then visit `https://YOUR-SERVICE.onrender.com/api/version`. The JSON must show `"top6CardHeader":"MOM + OB"` and `"legacyRankBadges":false`.
6. Open your dashboard and force refresh: **Ctrl+Shift+R** (Windows/Linux), **Cmd+Shift+R** (macOS). If the scores still do not appear, close all old dashboard tabs and reopen the site, or clear the site's cached data.
7. In browser Developer Tools, `document.documentElement.dataset.leaderCardUi` should return `"mom-ob-header-v2"`. If not, the browser is still running an older `app.js`.

Important: the live Kite feed, 20-day calculations, OB score, Top 6→Top 3 candidate logic, and chart auto-follow remain unchanged. This release fixes UI deployment/caching diagnostics only.
