"""KiteTicker-to-5m candle cache, with explicit incomplete/gap handling.

Only NSE cash ticks are used for price candles. Kite's volume_traded is a
cumulative *session* volume. Bar volumes are differences between consecutive
cumulative readings; data gaps and a mid-session first bucket are NOT guessed.
After history seeding, there is no scheduled historical polling. Recovery is
requested only after a missing interval or interrupted WebSocket session.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
BAR_SECONDS = 300
MARKET_OPEN_MINUTES = 9 * 60 + 15
MARKET_CLOSE_MINUTES = 15 * 60 + 30


def bucket_for(at: datetime) -> int | None:
    """NSE cash 5m buckets anchored to 09:15 India time, not UTC midnight."""
    at = at.astimezone(IST)
    minutes = at.hour * 60 + at.minute
    if at.weekday() >= 5 or not MARKET_OPEN_MINUTES <= minutes < MARKET_CLOSE_MINUTES:
        return None
    opening = datetime.combine(at.date(), time(9, 15), IST)
    return int(opening.timestamp()) + ((minutes - MARKET_OPEN_MINUTES) // 5) * BAR_SECONDS


def normalize_bar(bar: dict) -> dict:
    return {key: bar[key] for key in ('time', 'open', 'high', 'low', 'close', 'volume')}


class LiveFiveMinuteCache:
    """Thread-safe streaming bars, immutable copies returned to async scorer."""

    def __init__(self):
        self._lock = threading.RLock()
        self.day = None
        self.in_progress: dict[str, dict] = {}
        self.last_volume: dict[str, int] = {}
        self.last_bucket: dict[str, int] = {}
        self.closed: dict[str, dict[int, dict]] = defaultdict(dict)
        self.historical: dict[str, dict[int, dict]] = defaultdict(dict)
        self.seeded: set[str] = set()
        self.need_recovery: set[str] = set()
        self.bad_buckets: dict[str, set[int]] = defaultdict(set)
        self.closed_count = 0
        self.recoveries = 0
        self.disconnects = 0
        self.last_closed_at: int | None = None

    def reset(self, day):
        with self._lock:
            self.day = day
            self.in_progress.clear()
            self.last_volume.clear()
            self.last_bucket.clear()
            self.closed.clear()
            self.historical.clear()
            self.seeded.clear()
            self.need_recovery.clear()
            self.bad_buckets.clear()
            self.closed_count = 0
            self.last_closed_at = None

    def _ensure_day(self, day):
        if self.day != day:
            self.reset(day)

    def on_disconnect(self):
        with self._lock:
            self.disconnects += 1
            self.need_recovery.update(self.seeded)
            for item in self.in_progress.values():
                item['valid'] = False

    def seed(self, symbol: str, closed_today: list[dict], now: datetime):
        """Install authoritative historical closed bars without losing newer ticks."""
        with self._lock:
            self._ensure_day(now.astimezone(IST).date())
            for b in closed_today:
                if datetime.fromtimestamp(b['time'], IST).date() == self.day:
                    self.historical[symbol][b['time']] = normalize_bar(b)
                    self.bad_buckets[symbol].discard(b['time'])
            self.seeded.add(symbol)
            # If the exchange history covered missing intervals, recovery can end.
            self._clear_resolved_gaps(symbol)

    def _clear_resolved_gaps(self, symbol):
        if not self.bad_buckets[symbol]:
            self.need_recovery.discard(symbol)
        elif all(t in self.historical[symbol] for t in self.bad_buckets[symbol]):
            self.bad_buckets[symbol].clear()
            self.need_recovery.discard(symbol)

    def accept(self, key: str, tick: dict, at: datetime):
        """Ingest a quote from provider callback. Nothing on futures affects OHLC."""
        if not key.startswith('NSE:'):
            return
        symbol = key[4:]
        bucket = bucket_for(at)
        if bucket is None:
            return
        price = float(tick.get('last_price') or 0)
        if price <= 0:
            return
        volume_value = tick.get('volume_traded', tick.get('volume'))
        if volume_value is None:
            return  # Must not fabricate volume required for RVOL.
        cumulative = max(0, int(volume_value))
        with self._lock:
            self._ensure_day(at.astimezone(IST).date())
            previous_volume = self.last_volume.get(symbol)
            previous_bucket = self.last_bucket.get(symbol)
            # Cumulative volume shouldn't move backwards during an NSE session.
            if previous_volume is not None and cumulative < previous_volume:
                self.need_recovery.add(symbol)
                self.in_progress.pop(symbol, None)
                previous_volume = None
            if previous_bucket is not None and bucket < previous_bucket:
                return  # Out-of-order quote packet; never rewrite past bars.
            if previous_volume is None:
                # Opening bar starts at known daily volume=0. At a later time,
                # pre-connection trades cannot be assigned to this 5m bucket.
                is_opening = bucket == int(datetime.combine(self.day, time(9,15), IST).timestamp())
                delta = cumulative if is_opening else 0
                valid = is_opening
            else:
                delta = max(0, cumulative - previous_volume)
                valid = True
            if previous_bucket is not None and bucket > previous_bucket + BAR_SECONDS:
                for missing in range(previous_bucket + BAR_SECONDS, bucket, BAR_SECONDS):
                    self.bad_buckets[symbol].add(missing)
                self.need_recovery.add(symbol)
            current = self.in_progress.get(symbol)
            if current and current['time'] != bucket:
                self._finish(symbol, current)
                current = None
            if current is None:
                # Last delta across the boundary is allocated to the new bar;
                # this is an approximation because ticks may straddle 09:20.
                current = {'time':bucket, 'open':price,'high':price,'low':price,
                           'close':price,'volume':delta,'valid':valid}
                self.in_progress[symbol] = current
            else:
                current['high'] = max(current['high'], price)
                current['low'] = min(current['low'], price)
                current['close'] = price
                current['volume'] += delta
                current['valid'] = current['valid'] and valid
            self.last_volume[symbol] = cumulative
            self.last_bucket[symbol] = bucket

    def _finish(self, symbol, item):
        if item['valid'] and item['volume'] > 0:
            self.closed[symbol][item['time']] = normalize_bar(item)
            self.closed_count += 1
            self.last_closed_at = max(self.last_closed_at or 0, item['time'] + BAR_SECONDS)
        else:
            self.bad_buckets[symbol].add(item['time'])
            self.need_recovery.add(symbol)

    def close_due(self, now: datetime) -> set[str]:
        """Finalize only completed boundaries; never invent an empty bar."""
        with self._lock:
            changed = set()
            for symbol, current in list(self.in_progress.items()):
                if current['time'] + BAR_SECONDS <= now.timestamp():
                    self._finish(symbol, current)
                    if symbol in self.seeded:
                        changed.add(symbol)
                    self.in_progress.pop(symbol, None)
            return changed

    def bars(self, symbol: str) -> list[dict]:
        with self._lock:
            merged = dict(self.historical.get(symbol, {}))
            # Historical candle wins when reconciliation has supplied a row.
            merged.update({t:b for t,b in self.closed.get(symbol,{}).items() if t not in merged})
            return [dict(merged[t]) for t in sorted(merged)]

    def pending_recovery(self) -> set[str]:
        with self._lock:
            return set(self.need_recovery & self.seeded)

    def recovery_ready(self, symbol: str, now: datetime) -> bool:
        """Never ask Kite to repair a candle that has not closed yet."""
        with self._lock:
            if symbol not in self.need_recovery or symbol not in self.seeded:
                return False
            current = self.in_progress.get(symbol)
            if current and not current['valid'] and current['time'] + BAR_SECONDS > now.timestamp():
                return False
            return True

    def request_recovery(self, symbol):
        with self._lock:
            if symbol in self.seeded:
                self.need_recovery.add(symbol)

    def recovered(self, symbol: str, closed_today: list[dict], now: datetime):
        """Only mark repaired when Kite supplied every known missing candle."""
        with self._lock:
            outstanding = set(self.bad_buckets[symbol])
            self.seed(symbol, closed_today, now)
            unresolved = outstanding - self.historical[symbol].keys()
            if unresolved:
                self.bad_buckets[symbol] = unresolved
                self.need_recovery.add(symbol)
                return False
            self.bad_buckets[symbol].clear()
            self.need_recovery.discard(symbol)
            self.recoveries += 1
            return True

    def info(self) -> dict:
        with self._lock:
            return {'source':'kite_websocket', 'seeded':len(self.seeded),
                    'closed_tick_candles':self.closed_count,
                    'last_closed_at':self.last_closed_at,
                    'pending_recovery':len(self.need_recovery & self.seeded),
                    'recovery_fetches':self.recoveries,
                    'disconnects':self.disconnects}
