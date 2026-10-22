"""Adaptive intraday candidate ranking, independent of yesterday's closing gap.

A large move at 09:20 should not retain first place at 14:00 without
recent participation. The 5m/15m strength is repriced each quote; the
session-open move is a small, capped tie-breaker. Neither score is a win
probability or an entry/exit signal.
"""
from __future__ import annotations


def _float(value, default=0.0):
    try:
        value = float(value)
        return value if value == value and abs(value) != float('inf') else default
    except (TypeError, ValueError, OverflowError):
        return default


def adaptive_rank(row: dict, *, demo: bool = False) -> float:
    """Rank within a bullish/bearish pool; never use as trade confirmation.

    Intraday score 0..100 uses fresh 5m/15m moves, RVOL, VWAP and volume.
    Only 0..8 points come from the net move since the session open. This
    prevents a large *old* gap/spike from dominating fresh movement.
    """
    live = row.get('momentumScore100')
    if demo:
        return round(_float(live) + min(8.0, abs(_float(row.get('change'))) * 0.75), 2)
    fresh = _float(live)
    session_move = abs(_float(row.get('sessionMovePct')))
    # The opening impulse never contributes more than 8 points to selection.
    session_bonus = min(8.0, session_move * 1.4)
    state = row.get('momentumState', 'WARMING_UP')
    modifier = {'ACTIVE': 12.0, 'CONSOLIDATING': -8.0,
                'FADING': -24.0, 'WARMING_UP': -5.0,
                'STALE': -45.0}.get(state, -12.0)
    # An RVOL-confirmed ACTIVE stock gets a slight priority; other stocks
    # remain visible as WATCH candidates when there are too few confirmations.
    volume_bonus = 3.0 if row.get('volumeConfirmed') and state == 'ACTIVE' else 0.0
    return round(max(0.0, fresh + session_bonus + modifier + volume_bonus), 2)


def diversified_candidates(rows: list[dict], limit: int = 3) -> list[dict]:
    """Always fill from existing ranked stocks, preferring distinct sectors.

    ACTIVE plus RVOL-qualified rows get priority. Remaining cards are WATCH
    candidates with truthful status; they're not silently promoted to signals.
    Never fabricate stocks to fill a genuinely absent bullish/bearish pool.
    """
    ranked = sorted(rows, key=lambda r: (bool(r.get('pickEligible')),
                                               _float(r.get('adaptiveRankScore')),
                                               _float(r.get('score'))), reverse=True)
    chosen, sectors, selected = [], set(), set()
    active = [r for r in ranked if r.get('pickEligible')]
    watch = [r for r in ranked if not r.get('pickEligible')]
    # Fresh momentum takes precedence over sector diversity. Sector diversity
    # determines order within each eligible/watch cohort, not across them.
    for cohort in (active, watch):
        for stock in cohort:
            if stock['sector'] not in sectors and stock['symbol'] not in selected:
                chosen.append(stock)
                selected.add(stock['symbol'])
                sectors.add(stock['sector'])
            if len(chosen) == limit:
                return chosen
        for stock in cohort:
            if stock['symbol'] not in selected:
                chosen.append(stock)
                selected.add(stock['symbol'])
                sectors.add(stock['sector'])
            if len(chosen) == limit:
                return chosen
    return chosen
