"""Auditable 0–100 intraday momentum rating across 5m/15m/30m/60m.

The score is a heuristic, not a win probability. Longer windows are explicitly
missing until observed during the current session; an absent window receives
zero points rather than an estimated price move.
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
    """Score allocation: 5m=25, 15m=20, 30m=15, 1h=10, historical
    RVOL=15, volume acceleration=5, VWAP=7, breakout=3 (total 100).
    """
    if demo:
        change = _num(row.get('change'))
        rvol = _num(row.get('rvol'))
        oi = _num(row.get('oiChange'))
        parts = {'dayMove': round(65 * _cap(abs(change)/9.0), 1),
                 'demoVolume': round(21 * _cap(rvol/3.5), 1),
                 'vwap': 0.0, 'demoOi': round(4 * _cap(max(0,oi)/20.0),1)}
        vwap = row.get('vwap')
        if vwap is not None and ((_num(row.get('price')) >= _num(vwap)) == (change >= 0)):
            parts['vwap'] = 10.0
        return {'momentumScore100': min(100, round(sum(parts.values()))),
                'momentumScoreBasis': 'DEMO', 'momentumScoreBreakdown': parts}

    if row.get('momentumState') == 'STALE':
        return {'momentumScore100': None, 'momentumScoreBasis': 'STALE',
                'momentumScoreBreakdown': {}}

    direction = 1 if _num(row.get('change')) >= 0 else -1
    adr = max(0.0, _num(row.get('adr20')))
    specs = (
        ('recent5m', 'recent5mPct', 25, max(.10, adr*.055)),
        ('recent15m', 'recent15mPct', 20, max(.22, adr*.12)),
        ('recent30m', 'recent30mPct', 15, max(.32, adr*.18)),
        ('recent60m', 'recent60mPct', 10, max(.48, adr*.28)),
    )
    parts = {
        label: round(points * _cap(direction * _num(row.get(key)) / (3 * threshold)), 2)
        if row.get(key) is not None else 0.0
        for label, key, points, threshold in specs
    }
    parts.update({
        'rvol20': round(15 * _cap(_num(row.get('rvol'))/4.0), 2),
        'volumeAcceleration': round(5 * _cap(_num(row.get('recentVolumeRatio'))/3.0), 2),
        'vwapAlignment': 0.0,
        'breakout20': 0.0,
    })
    if row.get('price') is not None and row.get('vwap') is not None:
        aligned = ((_num(row['price']) >= _num(row['vwap'])) if direction > 0
                   else (_num(row['price']) <= _num(row['vwap'])))
        parts['vwapAlignment'] = 7.0 if aligned else 0.0
    if row.get('breakout20') == ('ABOVE_20D_HIGH' if direction > 0 else 'BELOW_20D_LOW'):
        parts['breakout20'] = 3.0

    score = max(0, min(100, round(sum(parts.values()))))
    state = row.get('momentumState')
    if state == 'FADING':
        score = min(score, 25)
    elif state == 'CONSOLIDATING':
        score = min(score, 42)
    elif state == 'ACTIVE' and not row.get('pickEligible'):
        score = min(score, 55)
    if row.get('recent5mPct') is None or row.get('recent15mPct') is None:
        score = min(score, 35)
        basis = 'LIVE_WARMUP'
    elif state != 'ACTIVE' or not row.get('pickEligible'):
        basis = 'LIVE_WATCH'
    else:
        basis = ('LIVE_20D' if _num(row.get('historySessions')) >= 20 and
                 _num(row.get('intradaySessions')) >= 20 else 'LIVE_PARTIAL_HISTORY')
    return {'momentumScore100': score, 'momentumScoreBasis': basis,
            'momentumScoreBreakdown': parts}
