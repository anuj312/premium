"""Rolling four-horizon intraday price momentum from genuine current-session ticks.

Windows are 5m/15m/30m/60m, evaluated against sampled prices from the
*same trading session*. Windows unavailable early in the day or after a
restart stay missing rather than falling back to previous-day prices.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
WINDOWS = (5, 15, 30, 60)
TAPE_LIMIT = 68  # 60 minutes of sampled prices + sparse-quote tolerance
REFERENCE_TOLERANCE_MINUTES = 1
MIN_RVOL_FOR_PICK = 1.0


def update_minute_tape(existing: list[dict] | None, when: datetime,
                       price: float, cumulative_volume: int | None) -> list[dict]:
    """One compact observation/minute per stock. Reset on the first new date."""
    local = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
    minute = int(local.timestamp() // 60)
    day = local.date().isoformat()
    tape = existing if existing and existing[-1].get('day') == day else []
    if tape and minute < tape[-1]['minute']:
        return tape
    point = {'minute': minute, 'day': day, 'price': float(price),
             'volume': int(cumulative_volume) if cumulative_volume is not None else None}
    if tape and tape[-1]['minute'] == minute:
        tape[-1] = point
    else:
        tape.append(point)
    return tape[-TAPE_LIMIT:]


def _point_at(tape: list[dict], minute: int) -> dict | None:
    for point in reversed(tape):
        if 0 <= minute - point['minute'] <= REFERENCE_TOLERANCE_MINUTES:
            return point
        if point['minute'] < minute - REFERENCE_TOLERANCE_MINUTES:
            break
    return None


def fresh_momentum(tape: list[dict] | None, price: float, change: float,
                   adr20: float | None = None, rvol20: float | None = None,
                   now: datetime | None = None) -> dict:
    """Four-horizon trend confirmation with truthful partial-session warmup.

    Eligibility requires confirmed 5m AND 15m price movement, historical RVOL,
    and agreement from whichever 30m/60m windows are actually available. Before
    enough session observations exist the longer windows are PENDING; never
    manufactured from historical candles or earlier trading days. A recent
    reversal or long-window conflict cannot qualify as ACTIVE.
    """
    empty = {'momentumState': 'WARMING_UP',
             'recent5mPct': None, 'recent15mPct': None,
             'recent30mPct': None, 'recent60mPct': None,
             'momentumWindowsReady': 0, 'momentumWindowsTotal': 4,
             'recentVolumeRatio': None, 'freshScore': 0.0, 'pickEligible': False,
             'volumeConfirmed': False,
             'freshReason': 'Waiting for 5m/15m current-session price history'}
    if not tape:
        return empty
    now = now or datetime.now(IST)
    local = now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    minute = int(local.timestamp() // 60)
    if tape[-1]['day'] != local.date().isoformat() or minute - tape[-1]['minute'] > 2:
        return {**empty, 'momentumState': 'STALE',
                'freshReason': 'No current-session quote in the last 2 minutes'}
    if minute < tape[-1]['minute']:
        return empty

    points = {window: _point_at(tape, minute - window) for window in WINDOWS}
    changes = {
        window: (100.0 * (price / point['price'] - 1)
                 if point and point.get('price', 0) > 0 else None)
        for window, point in points.items()
    }
    measures = {'recent5mPct': changes[5], 'recent15mPct': changes[15],
                'recent30mPct': changes[30], 'recent60mPct': changes[60]}
    completed = sum(value is not None for value in changes.values())
    progress = {'momentumWindowsReady': completed, 'momentumWindowsTotal': 4}
    if changes[5] is None or changes[15] is None:
        return {**empty, **measures, **progress,
                'freshReason': 'Waiting for 5m and 15m quotes in this session'}

    direction = 1 if change >= 0 else -1
    directed = {window: (direction * movement if movement is not None else None)
                for window, movement in changes.items()}
    adr = max(float(adr20 or 0), 0.0)
    thresholds = {5: max(.10, adr * .055), 15: max(.22, adr * .12),
                  30: max(.32, adr * .18), 60: max(.48, adr * .28)}
    # Longer timeframes confirm current trend rather than dominating with an
    # old opening move; weaker-than-quarter-threshold direction is unconfirmed.
    reversal = any(directed[w] is not None and directed[w] <= -thresholds[w]
                   for w in WINDOWS)
    short_active = (directed[5] >= thresholds[5]
                    and directed[15] >= thresholds[15])
    longer_agree = all(directed[w] is None or directed[w] >= .25 * thresholds[w]
                       for w in (30, 60))
    if reversal:
        state, reason = 'FADING', 'One or more rolling windows reverse the daily direction'
    elif short_active and longer_agree:
        state = 'ACTIVE'
        reason = f'5m + 15m strength confirmed; {completed}/4 same-session windows available'
    elif short_active:
        state, reason = 'CONSOLIDATING', 'Recent 5m/15m strength conflicts with 30m/1h trend'
    else:
        state, reason = 'CONSOLIDATING', 'Recent 5m/15m movement is too weak'

    latest_volume = tape[-1].get('volume')
    p5, p10 = points[5], _point_at(tape, minute-10)
    v5 = ((latest_volume - p5['volume']) if p5 and latest_volume is not None
          and p5['volume'] is not None and latest_volume >= p5['volume'] else None)
    prior5 = ((p5['volume'] - p10['volume']) if p5 and p10 and
              p5['volume'] is not None and p10['volume'] is not None and
              p5['volume'] >= p10['volume'] else None)
    volume_ratio = round(v5/prior5, 2) if v5 is not None and prior5 and prior5 > 0 else None
    volume_ok = rvol20 is not None and rvol20 >= MIN_RVOL_FOR_PICK
    eligible = state == 'ACTIVE' and volume_ok
    if state == 'ACTIVE' and not volume_ok:
        reason += ('; 20D RVOL not ready' if rvol20 is None else '; 20D RVOL below 1.0x')

    weights = {5: 20, 15: 16, 30: 12, 60: 10}
    freshness = (sum(weights[w] * min(3, max(0.0, directed[w]) / thresholds[w])
                     for w in WINDOWS if directed[w] is not None)
                 + 4 * min(3, float(rvol20 or 0))) if state == 'ACTIVE' else 0.0
    if state == 'ACTIVE' and volume_ratio is not None:
        freshness += 3 * min(3, volume_ratio)
    return {'momentumState': state,
            **{k: (round(v, 3) if v is not None else None) for k,v in measures.items()},
            **progress, 'recentVolumeRatio': volume_ratio,
            'freshScore': round(freshness, 2), 'pickEligible': eligible,
            'volumeConfirmed': volume_ok, 'freshReason': reason}
