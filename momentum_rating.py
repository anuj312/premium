"""Bounded, explainable 0–100 *momentum strength* display score.

It is NOT an estimated success probability or a trading recommendation.
Live ratings track the current 5m/15m conditions even when a stock is
SIDEWAYS, WARMING_UP, or below the RVOL confirmation threshold; such
ratings are explicitly observational, not validated entry signals. A simulated showcase
rating is explicitly labeled as DEMO and never used to validate live trades.
"""
from __future__ import annotations


def _num(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v and abs(v) != float('inf') else default
    except (TypeError, ValueError, OverflowError):
        return default


def _cap(value: float, upper: float = 1.0) -> float:
    return min(upper, max(0.0, value))


def momentum_rating(row: dict, *, demo: bool = False) -> dict:
    """Return a score on 0–100 and an auditable per-factor breakdown.

    Live factors (maximum points): 5m directional move 30, 15m directional
    move 25, time-matched 20-session RVOL 20, recent five-minute volume
    acceleration 10, directional VWAP 10, 20-session directional breakout 5.
    Both recent moves are normalized against ADR20-based noise thresholds.
    Missing optional volume acceleration / VWAP / breakout means 0 bonus,
    not an invented value. Stale quotes show no current rating; unconfirmed
    price action gets a bounded, explicitly provisional strength rating.
    """
    if demo:
        # Showcase-only visual data: never claim it was calculated from live
        # minute tape or a valid 20-session historical baseline.
        change = _num(row.get('change'))
        rvol = _num(row.get('rvol'))
        oi = _num(row.get('oiChange'))
        move_points = 65 * _cap(abs(change) / 9.0)
        volume_points = 21 * _cap(rvol / 3.5)
        vwap = row.get('vwap')
        aligned = vwap is not None and ((_num(row.get('price')) >= _num(vwap)) == (change >= 0))
        vwap_points = 10.0 if aligned else 0.0
        oi_points = 4 * _cap(max(0.0, oi) / 20.0)
        parts = {'dayMove': round(move_points, 1), 'demoVolume': round(volume_points, 1),
                 'vwap': round(vwap_points, 1), 'demoOi': round(oi_points, 1)}
        return {'momentumScore100': max(0, min(100, round(sum(parts.values())))),
                'momentumScoreBasis': 'DEMO', 'momentumScoreBreakdown': parts}

    if row.get('momentumState') == 'STALE':
        return {'momentumScore100': None, 'momentumScoreBasis': 'STALE',
                'momentumScoreBreakdown': {}}

    direction = 1 if _num(row.get('change')) >= 0 else -1
    five = direction * _num(row.get('recent5mPct'))
    fifteen = direction * _num(row.get('recent15mPct'))
    adr = max(0.0, _num(row.get('adr20')))
    threshold5 = max(0.10, adr * 0.055)
    threshold15 = max(0.22, adr * 0.12)
    rvol = _num(row.get('rvol'))
    acceleration = _num(row.get('recentVolumeRatio'))
    # Strong means approximately 3x the minimum move used for ACTIVE status.
    parts = {
        'recent5m': round(30 * _cap(five / (3 * threshold5)), 2),
        'recent15m': round(25 * _cap(fifteen / (3 * threshold15)), 2),
        'rvol20': round(20 * _cap(rvol / 4.0), 2),
        'volumeAcceleration': round(10 * _cap(acceleration / 3.0), 2),
        'vwapAlignment': 0.0,
        'breakout20': 0.0,
    }
    price = row.get('price')
    vwap = row.get('vwap')
    if price is not None and vwap is not None:
        aligned = (_num(price) >= _num(vwap)) if direction > 0 else (_num(price) <= _num(vwap))
        parts['vwapAlignment'] = 10.0 if aligned else 0.0
    if (row.get('breakout20') == ('ABOVE_20D_HIGH' if direction > 0 else 'BELOW_20D_LOW')):
        parts['breakout20'] = 5.0

    score = max(0, min(100, round(sum(parts.values()))))
    if row.get('momentumState') == 'FADING':
        score = min(score, 25)
    elif row.get('momentumState') == 'CONSOLIDATING':
        score = min(score, 42)
    elif row.get('momentumState') == 'ACTIVE' and not row.get('pickEligible'):
        score = min(score, 55)  # strong move, but insufficient historical RVOL
    if row.get('recent5mPct') is None or row.get('recent15mPct') is None:
        # No 15-minute tape yet. Do not imply a fully measured momentum score.
        score = min(score, 35)
        basis = 'LIVE_WARMUP'
    elif row.get('momentumState') != 'ACTIVE' or not row.get('pickEligible'):
        basis = 'LIVE_WATCH'
    else:
        basis = ('LIVE_20D' if _num(row.get('historySessions')) >= 20 and
             _num(row.get('intradaySessions')) >= 20 else 'LIVE_PARTIAL_HISTORY')
    return {'momentumScore100': score, 'momentumScoreBasis': basis,
            'momentumScoreBreakdown': parts}
