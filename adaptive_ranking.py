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


def top_momentum_candidates(rows: list[dict], limit: int = 3) -> list[dict]:
    """Select the three strongest displayed MOM scores from the current Top 6.

    Neither pick eligibility nor sector diversification overrides momentum.
    Missing/stale MOM numbers rank behind available scores; adaptive score and
    original leader score break ties only. Confirmation labels are independent.
    """
    return sorted(rows, key=lambda r: (
        _float(r.get('momentumScore100'), -1.0),
        _float(r.get('adaptiveRankScore')),
        _float(r.get('score')),
    ), reverse=True)[:limit]


def sort_picks_by_ob_score(picks: list[dict]) -> list[dict]:
    """Display selected picks by OB quality high to low, missing OB last.

    OB scores (including WAIT) are quality measurements, NOT entry signals.
    Momentum is the tie-breaker; this never changes which stocks were selected.
    """
    return sorted(picks, key=lambda r: (
        _float(r.get('obQualityScore'), -1.0),
        _float(r.get('momentumScore100'), -1.0),
        _float(r.get('adaptiveRankScore')),
    ), reverse=True)
