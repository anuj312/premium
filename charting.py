"""Multi-timeframe Kite OHLC candles and transparent price-action order-block candidates.

Order blocks are heuristics, not evidence of resting orders or institutions.
Zone creation uses COMPLETED candles only. No look-ahead beyond confirmation.
"""
from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, time
from math import ceil
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
INTERVAL_SECONDS = 300
# Only allow explicitly supported Kite historical-data intervals. Keep requests bounded.
CHART_INTERVALS = {
    '1m': ('minute', 60, 7),
    '3m': ('3minute', 180, 14),
    '5m': ('5minute', 300, 20),
    '15m': ('15minute', 900, 40),
    '30m': ('30minute', 1800, 60),
    '1D': ('day', 86400, 240),
}



def as_unix(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return int(value.timestamp())


def normalize_candles(rows):
    """Convert Kite historical_data() objects to sorted epoch OHLCV candles."""
    result = {}
    for raw in rows:
        try:
            t = as_unix(raw['date'])
            o, h, l, c = (float(raw[x]) for x in ('open', 'high', 'low', 'close'))
            volume = max(0, int(raw.get('volume') or 0))
            if o <= 0 or l <= 0 or h < max(o, c, l) or l > min(o, c) or c <= 0:
                continue
            result[t] = {'time': t, 'open': round(o, 2), 'high': round(h, 2),
                         'low': round(l, 2), 'close': round(c, 2), 'volume': volume}
        except (ValueError, TypeError, KeyError):
            continue
    return [result[key] for key in sorted(result)]


def last_completed_count(candles, now=None, interval_seconds=INTERVAL_SECONDS):
    if not candles:
        return 0
    now = now or datetime.now(IST)
    now_ts = now.timestamp()
    last = datetime.fromtimestamp(candles[-1]['time'], IST)
    if interval_seconds == 86400:
        close = datetime.combine(last.date(), time(15, 30), IST).timestamp()
    else:
        session_close = datetime.combine(last.date(), time(15, 30), IST).timestamp()
        close = min(candles[-1]['time'] + interval_seconds, session_close)
    return len(candles) - (1 if close > now_ts else 0)


def identify_order_blocks(candles, completed=None, lookback=90, max_per_side=3):
    """The last opposite-colored candle before a confirmed strong swing breakout.

    Bullish: bearish origin candle, then a bullish candle in next 1-3 closed
    bars closes above preceding 6-bar swing high with body >= 0.65 ATR and
    displacement >= 1.0 ATR. Zone: [origin low, origin open].
    Bearish is mirrored: zone [origin open, origin high].
    A zone is invalidated by a later COMPLETED CLOSE beyond its far boundary;
    touching it only marks it as tested. These are price-action *candidates*.
    """
    n = min(len(candles), completed if completed is not None else len(candles))
    if n < 12:
        return []
    blocks = []
    start = max(7, n - lookback)
    for i in range(start, n - 1):
        origin = candles[i]
        prior = candles[max(0, i - 14):i]
        ranges = [max(.01, r['high'] - r['low']) for r in prior]
        atr = sum(ranges) / len(ranges)
        if atr <= 0:
            continue
        swing_hi = max(c['high'] for c in candles[i-6:i+1])
        swing_lo = min(c['low'] for c in candles[i-6:i+1])
        direction = 'bullish' if origin['close'] < origin['open'] else ('bearish' if origin['close'] > origin['open'] else None)
        if not direction:
            continue
        confirm = None
        for j in range(i+1, min(i+4, n)):
            probe = candles[j]
            body = abs(probe['close'] - probe['open'])
            if body < atr * .65:
                continue
            if (direction == 'bullish' and probe['close'] > swing_hi
                    and probe['close'] - origin['close'] >= atr
                    and probe['close'] > probe['open']):
                confirm = j
                break
            if (direction == 'bearish' and probe['close'] < swing_lo
                    and origin['close'] - probe['close'] >= atr
                    and probe['close'] < probe['open']):
                confirm = j
                break
        if confirm is None:
            continue
        low = origin['low'] if direction == 'bullish' else origin['open']
        high = origin['open'] if direction == 'bullish' else origin['high']
        if high <= low:
            continue
        invalidated = False
        tested = False
        for c in candles[confirm+1:n]:
            if (direction == 'bullish' and c['close'] < low) or (direction == 'bearish' and c['close'] > high):
                invalidated = True
                break
            if (direction == 'bullish' and c['low'] <= high) or (direction == 'bearish' and c['high'] >= low):
                tested = True
        if invalidated:
            continue
        blocks.append({
            'type': direction,
            'origin_time': origin['time'], 'confirmed_time': candles[confirm]['time'],
            'low': round(low, 2), 'high': round(high, 2),
            'tested': tested, 'status': 'tested' if tested else 'fresh',
        })
    out = []
    for side in ('bullish', 'bearish'):
        unique = []
        for b in sorted((x for x in blocks if x['type'] == side), key=lambda x: x['origin_time'], reverse=True):
            # Suppress nearly identical nested zones originating in the same structure.
            if any(b['low'] <= x['high'] and b['high'] >= x['low'] for x in unique):
                continue
            unique.append(b)
            if len(unique) == max_per_side:
                break
        out.extend(unique)
    return sorted(out, key=lambda x: x['origin_time'])


def demo_candles(symbol: str, anchor_price: float | None = None, now=None, timeframe='5m'):
    """Deterministic synthetic multi-session candles at selected interval.

    Generated only in MODE=demo, never used as a fallback from failed Kite.
    """
    if timeframe not in CHART_INTERVALS:
        raise ValueError('Unsupported chart timeframe')
    interval, seconds, _ = CHART_INTERVALS[timeframe]
    now = (now or datetime.now(IST)).astimezone(IST)
    rng = random.Random(zlib.crc32((symbol + timeframe).encode('utf-8')))
    base = max(5., float(anchor_price or rng.uniform(300, 1800)))
    session_count = {'1m': 7, '3m': 11, '5m': 27, '15m': 25, '30m': 36, '1D': 160}[timeframe]
    days = []
    day = now.date()
    while len(days) < session_count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    price = base * .967
    rows = []
    minutes = seconds // 60
    bars_per_day = 1 if interval == 'day' else ceil(375 / minutes)
    for d in days:
        if interval == 'day':
            # Kite represents daily candles at local midnight.
            stamp = datetime.combine(d, time(0, 0), IST)
            o = price
            delta = rng.gauss(.14, .9) * base * .009
            c = max(1.0, o + delta)
            wick = base * rng.uniform(.004, .012)
            rows.append({'date': stamp, 'open': o, 'high': max(o,c)+wick,
                         'low': max(.01,min(o,c)-wick), 'close': c,
                         'volume': rng.randint(750000, 2800000)})
            price = c
            continue
        open_at = datetime.combine(d, time(9, 15), IST)
        for idx in range(bars_per_day):
            stamp = open_at + timedelta(minutes=minutes*idx)
            if d == now.date() and stamp > now:
                break
            amplitude = base * .00095 * (seconds / 300) ** .35
            phase = (idx + days.index(d)*7) % 31
            impulse = (amplitude * 3.8 if phase in (7,8) else -amplitude*4.4 if phase in (22,23) else 0)
            delta = rng.gauss(.09,.65)*amplitude + impulse
            o = price
            c = max(1.0, o + delta)
            wick = amplitude * rng.uniform(.20, 1.1)
            rows.append({'date': stamp, 'open': o, 'high': max(o,c)+wick,
                         'low': max(.01,min(o,c)-wick*rng.uniform(.6,1.3)),
                         'close': c, 'volume': rng.randint(2500, 120000)*max(1,minutes//5)})
            price = c
    normalized = normalize_candles(rows)
    if normalized:
        ratio = base / normalized[-1]['close']
        for c in normalized:
            for field in ('open','high','low','close'):
                c[field] = round(c[field]*ratio,2)
    return normalized
