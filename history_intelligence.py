"""Twenty-completed-session price/volume baselines for the NSE cash universe.

Calculations use only finished days before the target trading date. No lookahead,
no synthetic data in Kite mode, and no orders or Kite credentials stored in cache.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
LOOKBACK = 20
CACHE_VERSION = 3


def session_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return (value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)).date()
    return date.fromisoformat(str(value)[:10])


def make_baseline(daily: list[dict], five_minute: list[dict], target_date: date) -> dict:
    """ADR20, ATR14, prior-20D H/L, volume at matching 5m session minute.

    Daily bars define 20 completed sessions. An incomplete set stays explicitly
    PARTIAL; historical intraday volume is never extrapolated from a daily total.
    """
    target_date = session_day(target_date)
    finished = sorted((d for d in daily if session_day(d['date']) < target_date),
                      key=lambda d: session_day(d['date']))
    # Deduplicate dates without counting a day twice.
    daily_by_date = {session_day(d['date']): d for d in finished}
    days = [daily_by_date[k] for k in sorted(daily_by_date)][-LOOKBACK:]
    dates = [session_day(d['date']) for d in days]
    count = len(days)
    daily_volumes = [float(d.get('volume') or 0) for d in days if float(d.get('volume') or 0) > 0]
    adr = sum((float(d['high']) - float(d['low'])) / max(float(d['close']), .01) * 100
              for d in days) / count if count else None
    high20 = max((float(d['high']) for d in days), default=None)
    low20 = min((float(d['low']) for d in days), default=None)
    # True range compares to preceding day's close; missing extra lookback uses
    # candle range rather than claiming an exact prior-close TR.
    complete_days = [daily_by_date[k] for k in sorted(daily_by_date)]
    all_finished = sorted(finished, key=lambda d: session_day(d['date']))
    earlier = [d for d in all_finished if session_day(d['date']) < (dates[0] if dates else target_date)]
    previous = float(earlier[-1]['close']) if earlier else None
    true_ranges = []
    for d in complete_days:
        hi, lo = float(d['high']), float(d['low'])
        true_ranges.append(max(hi-lo, abs(hi-previous), abs(lo-previous)) if previous is not None else hi-lo)
        previous = float(d['close'])
    atr = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else None

    session_bars = defaultdict(dict)
    allowed = set(dates)
    for bar in five_minute:
        stamp = bar.get('date', bar.get('time'))
        if not stamp:
            continue
        dt = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
        dt = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
        d = dt.date()
        minute = (dt.hour * 60 + dt.minute) - (9 * 60 + 15)
        if d in allowed and 0 <= minute < 375 and minute % 5 == 0:
            session_bars[d][minute] = max(0, float(bar.get('volume') or 0))
    # Require >=60 of the 75 five-minute buckets as protection against severely
    # partial/halted sessions, and never call a single bar a full reference day.
    valid = [d for d in dates if len(session_bars[d]) >= 60]
    # O(sessions × 75) rather than repeatedly re-summing older bars in each
    # bucket (a significant CPU saving on the 0.1-CPU Render Free instance).
    profile = {}
    if valid:
        totals = [0.0] * 75
        for d in valid:
            running = 0.0
            session = session_bars[d]
            for i in range(75):
                running += session.get(i * 5, 0.0)
                totals[i] += running
        profile = {str(i * 5): round(total / len(valid), 2)
                   for i, total in enumerate(totals)}

    return {'version': CACHE_VERSION, 'targetDate': target_date.isoformat(),
            'sessions': count, 'intradaySessions': len(valid),
            'status': 'READY' if count >= LOOKBACK and len(valid) >= LOOKBACK else 'PARTIAL',
            'lastCompleteSession': dates[-1].isoformat() if dates else None,
            'adr20': round(adr, 4) if adr is not None else None,
            'atr14': round(atr, 4) if atr is not None else None,
            'high20': round(high20, 4) if high20 is not None else None,
            'low20': round(low20, 4) if low20 is not None else None,
            'avgVolume20': round(sum(daily_volumes)/len(daily_volumes)) if daily_volumes else None,
            'volumeProfile': profile}


def live_features(baseline: dict | None, price: float, previous_close: float,
                  cumulative_volume: int | None, quote_time: datetime) -> dict:
    if not baseline:
        return {'adr20': None, 'atr14': None, 'high20': None, 'low20': None,
                'rvol': None, 'rangeMultiple': None, 'breakout20': None,
                'historySessions': 0, 'intradaySessions': 0, 'historyStatus': 'LOADING'}
    adr = baseline.get('adr20')
    # Never carry yesterday's volume-time curve into the next trading day.
    if baseline.get('targetDate') != session_day(quote_time).isoformat():
        return {'adr20': None, 'atr14': None, 'high20': None, 'low20': None,
                'rvol': None, 'rangeMultiple': None, 'breakout20': None,
                'historySessions': 0, 'intradaySessions': 0, 'historyStatus': 'REFRESHING'}
    move = abs((price / previous_close - 1) * 100) if previous_close > 0 else 0
    q = quote_time.replace(tzinfo=IST) if quote_time.tzinfo is None else quote_time.astimezone(IST)
    minute = (q.hour * 60 + q.minute) - (9 * 60 + 15)
    minute = max(0, min(370, minute - minute % 5))
    ref_vol = baseline.get('volumeProfile', {}).get(str(minute))
    rvol = round(cumulative_volume / ref_vol, 3) if (cumulative_volume is not None and
            baseline.get('intradaySessions', 0) >= 10 and ref_vol and ref_vol > 0) else None
    high, low = baseline.get('high20'), baseline.get('low20')
    breakout = 'ABOVE_20D_HIGH' if high is not None and price > high else (
               'BELOW_20D_LOW' if low is not None and price < low else None)
    return {'adr20': adr, 'atr14': baseline.get('atr14'), 'high20': high,
            'low20': low, 'rvol': rvol, 'rangeMultiple': round(move / adr, 3) if adr and adr>0 else None,
            'breakout20': breakout, 'historySessions': baseline.get('sessions', 0),
            'intradaySessions': baseline.get('intradaySessions', 0),
            'historyStatus': baseline.get('status', 'PARTIAL')}


def cache_file(directory: Path, symbol: str, target_date: date) -> Path:
    # Only NSE exact tickers from allowlist should reach this function.
    if not symbol or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-&' for ch in symbol):
        raise ValueError('Unsafe symbol for cache path')
    return directory / f'{symbol}_{target_date.isoformat()}_v{CACHE_VERSION}.json.gz'


def save_cache(directory: Path, symbol: str, target_date: date, baseline: dict,
               candles: list[dict] | None = None) -> None:
    """Persist compact baselines; bulk scans deliberately omit candle archives.

    Legacy callers may still pass candles for offline exports/tests. The free
    loader saves only the small derived baseline, so a warm instance consumes
    less I/O and disk even when the 20-session data is large.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = cache_file(directory, symbol, target_date)
    # Date objects are serialized as ISO strings; cache contains only public market history.
    payload = {'baseline': baseline}
    if candles is not None:
        payload['candles'] = [
            {k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
             for k, v in c.items()} for c in candles]
    temporary = path.with_suffix('.tmp')
    with gzip.open(temporary, 'wt', encoding='utf-8') as file:
        json.dump(payload, file, separators=(',', ':'))
    os.replace(temporary, path)


def load_cache(directory: Path, symbol: str, target_date: date) -> dict | None:
    path = cache_file(directory, symbol, target_date)
    if not path.exists():
        return None
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as file:
            data = json.load(file)
        if (data.get('baseline', {}).get('targetDate') == target_date.isoformat()
                and data['baseline'].get('version') == CACHE_VERSION):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
