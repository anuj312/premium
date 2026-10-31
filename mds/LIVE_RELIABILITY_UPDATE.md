# Live reliability update — 30 October 2026

This release implements the first five agreed improvements. Scoring weights,
current-versus-daily direction, time-based challenger replacement and outcome
replay remain future work. MOM and OB are heuristic scores, not probabilities.

## Changes

1. RVOL compares cumulative authoritative completed five-minute session volume
   against the historical cumulative profile at the same candle CLOSE boundary.
   Live partial-candle volume is never used as the numerator. API fields
   `rvolAsOf` and `rvolBasis` expose the cutoff; the detail panel displays it.
   RVOL stays unavailable until both historical baselines and completed session
   bars exist. At 09:21 it can use the 09:20 close, never the 09:25 close.
2. Live READY and eligible WAIT candidates both require current momentum
   eligibility. Invalid, stale, wrong-side and momentum-ineligible members leave
   immediately. Empty slots remain placeholders; valid replacements still use
   the existing three-revision stability rule. Disconnected/closed feeds clear
   live Top 3 membership. Demo remains explicitly simulated.
3. Cash subscriptions use full packets. Missing exchange/provider timestamps are
   rejected rather than replaced with server time. Receipt time is stored
   separately. Candidate quotes must be no more than 30 seconds old, have verified
   timestamps, recovered history and an RVOL cutoff no more than 420 seconds old.
4. A background worker repairs session minute history after startup/reconnect,
   checks missing minutes, repairs five-minute bars, and prevents eligibility
   until recovery succeeds. Session minute observations are checkpointed atomically
   under HISTORY_CACHE_DIR and restored only for the same date; restored data
   alone never authorizes a signal. Session rollover clears recovery and RVOL.
5. History preparation runs independently of chart clicks. Current leaders and
   existing picks are prioritized, then all mapped symbols. Five-minute session
   history refreshes once per observed five-minute bucket. All requests share
   the existing serial rate limiter. Signal preparation does not evict an open
   chart from the chart LRU cache. Completed-bar corrections clear the OB cache.

## Operation

Use the existing Kite environment configuration and start command. From premium:

```bash
python3 -m pip install -r requirements.txt
python3 server.py
```

Default local URL: http://127.0.0.1:8000 . Default mode remains demo.
For live data configure MARKET_MODE=kite, KITE_API_KEY and KITE_ACCESS_TOKEN in
.env or the server environment. Do not put credentials in browser JavaScript.
Set HISTORY_CACHE_DIR to durable storage to preserve checkpoints across deploys.
Only run one feed worker per cache directory.

The new recovery worker increases historical API use: normally one session-bar
request per mapped symbol per five-minute bucket, plus minute-history recovery
on startup/reconnect. Existing history warmup shares the same rate limiter, so
initial preparation may take several minutes. API failures or missing bars can
leave slots empty. No history is invented to fill them. In-progress chart bars
remain provisional; signals use completed bars. An exchange holiday calendar
and performance calibration against actual live traffic remain future work.

## Validation

111 offline unit/integration tests passed, including seven new regression tests
for timestamp rejection, RVOL cutoffs, momentum eligibility, click-independent
recovery, reconnect invalidation, missing history, checkpoint restoration,
session rollover and per-symbol staleness. Existing fixtures were updated where
old behavior intentionally changed. JavaScript syntax check also passed.

```bash
python3 -m pip install httpx
python3 -m unittest discover -s mds -p 'test_*.py' -q
node --check static/app.js
```

No authenticated Kite session or live-market replay was available here. This
release improves data consistency and eligibility checks; predictive accuracy
or profitability has not been measured.
