# Verification

Validated in this build:

- 105 isolated Python regression tests passed.
- Frontend JavaScript syntax check passed.
- Gunicorn application configuration/import check passed.
- Real-browser desktop and 390px mobile checks passed.
- Liquidity appears directly after % Change and renders at most five per side.
- A stale high-scoring liquidity row is excluded immediately.
- Expired market context removes dependent booster candidates.
- Request failure removes Live status and disables actionable panels.
- Regular-mode previous daily features remain visible with a fresh quote.
- Mobile layout has no document-width overflow; sticky navigation does not
  cover section headings when navigating.

Browser checks used explicitly labelled synthetic test fixtures only. Those
fixtures are NOT included in the production page, and no demo fallback exists.

A synthetic 198-stock computation benchmark measured approximately 11.3 seconds
for a cold feature rebuild and 0.22 seconds for a cached quote/depth refresh on
the test server. These are not hosting guarantees or broker latency measurements.
New bars/backfills require feature rebuilding; the app exposes source/cache ages
and invalidates stale results rather than promising an exact refresh interval.

Not validated: authenticated live Kite integration, official exchange calendar
coverage, real-order fills, or predictive profitability. Those require the user's
valid subscription/data and chronological cost-adjusted testing. Booster rules
remain experimental. No real broker credentials were included or used.

Re-run with `python3 -m unittest discover -s tests -v` after installing requirements.
