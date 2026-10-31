"""Stable dynamic Top 3: OB eligibility and consecutive-feed replacement logic.

Only eligible leaders can join. Existing picks stay visible while a challenger
must win across three distinct market-data revisions before replacing one.
"""
from __future__ import annotations

import math

REPLACEMENT_UPDATES = 3


def valid_score(row):
    value = row.get('obQualityScore')
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def eligible_ob_pick(row: dict, direction: str, *, market_live: bool, demo: bool) -> bool:
    """A scored zone is NOT automatically confirmed just because OB is numeric.

    READY can qualify in demo or when the feed is LIVE. WAIT may qualify only
    as a WATCH candidate with active momentum and a currently formed,
    non-invalidated zone (not stale, expired, or missing). INVALID never joins.
    """
    if not valid_score(row) or row.get('pickDataStale'):
        return False
    if (row.get('change', 0) >= 0) != (direction == 'bullish'):
        return False
    if row.get('obDirection') != direction:
        return False
    zone = row.get('obZone')
    if not isinstance(zone, dict) or zone.get('invalidated'):
        return False
    if not (demo or market_live):
        return False
    if not demo and not row.get('pickEligible'):
        return False
    if row.get('obState') == 'READY':
        return True
    if row.get('obState') != 'WAIT' or demo or not row.get('pickEligible'):
        return False
    # A WAIT score from a stale/expired pattern or missing validation must not
    # displace a real pick, even if the raw quality score is high.
    if row['obQualityScore'] < 60:
        return False
    reason = row.get('obReason') or ''
    return reason.startswith(('Fresh zone identified;',
                              'Retest lacks a recent confirmation'))


def update_members(names: list[str], pending: dict | None, leaders: list[dict],
                   all_current: dict[str, dict], *, revision: int, last_revision: int | None,
                   direction: str, market_live: bool, demo: bool):
    """Return (members, pending, checked_revision); mutate no external lists.

    Eligible leaders are prefiltered and sorted by OB quality descending.
    Data refresh/read-only HTTP snapshots with an unchanged revision cannot
    advance a replacement confirmation. The three slots remain sticky until
    a challenger wins in REPLACEMENT_UPDATES distinct revisions.
    """
    names = [s for s in names[:3] if s in all_current and eligible_ob_pick(
        all_current[s], direction, market_live=market_live, demo=demo)]
    leaders = [r for r in leaders if eligible_ob_pick(
        r, direction, market_live=market_live, demo=demo)]
    if len(names) < 3:
        names.extend(row['symbol'] for row in leaders if row['symbol'] not in names)  # max 3
        names = names[:3]
        return names, None, revision
    outsider = next((row for row in leaders if row['symbol'] not in names), None)
    if outsider is None:
        return names, None, revision
    # Incumbent's score is current (even when outside Top 6), not a stale
    # snapshot from when it first entered. Invalid or wrong-side = lowest.
    def incumbent_score(symbol):
        row = all_current.get(symbol)
        if row is None or not eligible_ob_pick(row, direction, market_live=market_live, demo=demo):
            return float('-inf')
        return row['obQualityScore']
    weakest = min(names, key=lambda symbol: (incumbent_score(symbol), names.index(symbol)))
    if outsider['obQualityScore'] <= incumbent_score(weakest):
        return names, None, revision
    if pending and pending.get('challenger') == outsider['symbol'] and pending.get('incumbent') == weakest:
        # Neither HTTP polling nor another client asking for /api/state can
        # invent a fresh feed update. Only newer revisions count.
        if last_revision is not None and revision <= last_revision:
            return names, pending, last_revision
        count = pending['count'] + 1
    else:
        count = 1
    if count >= REPLACEMENT_UPDATES:
        names[names.index(weakest)] = outsider['symbol']
        return names, None, revision
    return names, {'challenger': outsider['symbol'], 'incumbent': weakest, 'count': count}, revision
