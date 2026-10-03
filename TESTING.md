# Verification

Validated in this build:

- 152 isolated Python regression tests passed, including 17 private access-list
  tests covering Render secret-file loading, configured paths, formatting,
  missing/empty/unreadable files, precedence and denied access.
- 30 building-watchlist tests cover bullish/bearish symmetry, hard gates,
  completed-candle coverage, checklist readiness, unbroken range boundaries,
  freshness/expiry and non-mutating API invalidation.
- Frontend JavaScript syntax check passed.
- Gunicorn application configuration/import check passed.
- Real-browser desktop and 390px mobile checks passed in dark and light themes.
- Building checklists also fit a 320px phone viewport without document-width or
  checklist-label overflow. These are browser simulations, not physical-device tests.
- 43 synthetic browser assertions passed for Building: five-per-side limits,
  checks/proximity/symbol ranking, seven Pass/Wait checks, partial readiness,
  filters, scope restrictions, stale/malformed exclusions and offline removal.
- Building follows Liquidity immediately; its cards disappear at expiry without
  another API response. Checks are explicitly labelled unconfirmed, not entries.
- Liquidity appears directly after % Change and renders at most five per side.
- A stale high-scoring liquidity row is excluded immediately.
- Expired market context removes dependent booster candidates.
- Request failure removes Live status and disables actionable panels.
- Regular-mode previous daily features remain visible with a fresh quote.
- Mobile layout has no document-width overflow; sticky navigation does not
  cover section headings when navigating.
- Browser login checks distinguish missing-list HTTP 503 from unapproved-number
  HTTP 403 without granting access in either case.

Browser checks used explicitly labelled synthetic test fixtures only. Those
fixtures are NOT included in the production page, and no demo fallback exists.

An earlier synthetic 198-stock computation benchmark measured approximately 11.3 seconds
for a cold feature rebuild and 0.22 seconds for a cached quote/depth refresh on
the test server before the building feature was added. These are not hosting
guarantees or broker latency measurements for this build.
New bars/backfills require feature rebuilding; the app exposes source/cache ages
and invalidates stale results rather than promising an exact refresh interval.

Not validated: authenticated live Kite integration, official exchange calendar
coverage, real-order fills, or predictive profitability. Those require the user's
valid subscription/data and chronological cost-adjusted testing. Booster rules
and Building rules remain experimental. No real broker credentials were included or used.
The user's live Render service was not inspected or modified; secret-file
creation and deployment must be completed in that service's Render settings.

Re-run with `python3 -m unittest discover -s tests -v` after installing requirements.
