"""Fresh intraday momentum, independent of day-to-date performance.

Only real within-session, minute-sampled prices are used. A restarted process
must warm up again rather than treating previous-session candles as fresh.
Research screening signals; not order-entry recommendations.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
TAPE_LIMIT = 24  # about 24 minutes * one entry/minute * 198 stocks
REFERENCE_TOLERANCE_MINUTES = 1  # never substitute a very old price
MIN_RVOL_FOR_PICK = 1.0


def update_minute_tape(existing: list[dict] | None, when: datetime,
                       price: float, cumulative_volume: int | None) -> list[dict]:
    """Keep an O(1)-size rolling record; replace latest quote of each minute."""
    local = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
    key = int(local.timestamp() // 60)
    day = local.date().isoformat()
    tape = existing if existing and existing[-1].get('day') == day else []
    if tape and key < tape[-1]['minute']:
        return tape  # ignore old replay messages
    point = {'minute': key, 'day': day, 'price': float(price),
             'volume': int(cumulative_volume) if cumulative_volume is not None else None}
    if tape and tape[-1]['minute'] == key:
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
    """Classify true trailing ~5m/~15m price changes (not day-to-date returns).

    Sideways or countertrend moves cannot qualify for picks. 20-session volume
    RVOL >=1.0 is required for an actionable pick; incomplete history is
    marked unverified, not silently assumed to be high-volume.
    """
    empty = {'momentumState': 'WARMING_UP', 'recent5mPct': None,
             'recent15mPct': None, 'recentVolumeRatio': None,
             'freshScore': 0.0, 'pickEligible': False,
             'volumeConfirmed': False, 'freshReason': 'Collecting 15 minutes of live quotes'}
    if not tape:
        return empty
    now = now or datetime.now(IST)
    current_minute = int(now.timestamp() // 60)
    if tape[-1]['day'] != now.astimezone(IST).date().isoformat() or current_minute-tape[-1]['minute'] > 2:
        return {**empty, 'momentumState':'STALE', 'freshReason':'No current-session quote in the last 2 minutes'}
    if current_minute < tape[-1]['minute']:
        return empty
    p5, p15, p10 = (_point_at(tape, current_minute-offset) for offset in (5, 15, 10))
    if not p5 or not p15:
        return empty
    change5 = 100 * (price / p5['price'] - 1) if p5['price'] > 0 else 0.0
    change15 = 100 * (price / p15['price'] - 1) if p15['price'] > 0 else 0.0
    direction = 1 if change >= 0 else -1
    d5, d15 = direction*change5, direction*change15
    # Scale for a stock's typical 20-day volatility; fixed floors suppress noise.
    adr = max(float(adr20 or 0), 0.0)
    threshold5 = max(.10, adr*.055)
    threshold15 = max(.22, adr*.12)
    latest_volume = tape[-1].get('volume')
    v5 = ((latest_volume-p5['volume']) if latest_volume is not None and p5['volume'] is not None
          and latest_volume >= p5['volume'] else None)
    earlier_v5 = ((p5['volume']-p10['volume']) if p5 and p10 and
                  p5['volume'] is not None and p10['volume'] is not None and
                  p5['volume'] >= p10['volume'] else None)
    volume_ratio = (round(v5/earlier_v5, 2) if v5 is not None and earlier_v5 and earlier_v5>0 else None)
    if d5 <= -threshold5 or d15 <= -threshold15:
        state, reason = 'FADING', 'Fresh price movement is reversing the daily direction'
    elif d5 >= threshold5 and d15 >= threshold15:
        state, reason = 'ACTIVE', 'Fresh 5m and 15m momentum agree with daily direction'
    else:
        state, reason = 'CONSOLIDATING', 'Recent 5m/15m price movement is too weak'
    volume_ok = rvol20 is not None and rvol20 >= MIN_RVOL_FOR_PICK
    eligible = state == 'ACTIVE' and volume_ok
    if state == 'ACTIVE' and not volume_ok:
        reason += ('; 20D RVOL not ready' if rvol20 is None else '; 20D RVOL below 1.0x')
    freshness = 0.0
    if state == 'ACTIVE':
        freshness = 20*min(4, d5/threshold5)+10*min(4, d15/threshold15)
        freshness += 5*min(3, float(rvol20 or 0))
        if volume_ratio is not None:
            freshness += 3*min(3, volume_ratio)
    return {'momentumState':state, 'recent5mPct':round(change5, 3),
            'recent15mPct':round(change15, 3), 'recentVolumeRatio':volume_ratio,
            'freshScore':round(freshness, 2), 'pickEligible':eligible,
            'volumeConfirmed':volume_ok, 'freshReason':reason}
