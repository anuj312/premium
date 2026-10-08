"""Previous-20-completed-trading-session baseline for intraday relative momentum.

The baseline is mean absolute close-to-close daily % change over 20 previous
sessions. A complete window needs 21 prior daily closing prices, never today's
incomplete daily candle. The score is signed to preserve bullish/bearish direction.
"""
from __future__ import annotations
import hashlib
import random
from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
PERIOD = 20


def daily_baseline(rows: list[dict], today: date) -> float | None:
    prior = {}
    for row in rows:
        day = row.get('date')
        if isinstance(day, datetime):
            day = day.astimezone(IST).date() if day.tzinfo else day.date()
        elif isinstance(day, str):
            try:
                day = date.fromisoformat(day[:10])
            except ValueError:
                continue
        if not isinstance(day, date) or day >= today:
            continue
        try:
            price = float(row['close'])
        except (KeyError, ValueError, TypeError):
            continue
        if price > 0:
            prior[day] = price
    closes = [prior[d] for d in sorted(prior)[-(PERIOD+1):]]
    if len(closes) != PERIOD+1:
        return None
    average = sum(abs(b/a-1)*100 for a,b in zip(closes,closes[1:])) / PERIOD
    return round(average,5) if average > 0 else None


def momentum_multiple(today_change_pct: float | None, baseline_pct: float | None) -> float | None:
    if today_change_pct is None or baseline_pct is None or baseline_pct <= 0:
        return None
    return round(float(today_change_pct)/baseline_pct, 2)


def demo_baselines(symbols: set[str]) -> dict[str,float]:
    """Stable simulation; not actual NSE historical data."""
    result={}
    for sym in sorted(symbols):
        seed=int(hashlib.sha256(sym.encode()).hexdigest()[:12],16)
        rng=random.Random(seed)
        result[sym] = round(rng.uniform(0.65,3.1),5)
    return result
