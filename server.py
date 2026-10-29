"""OrderFlow Studio – NSE scanner with demo, authenticated external and Kite live feeds.

Run: pip install -r requirements.txt && python server.py
For Kite live: MARKET_MODE=kite plus server-only KITE_API_KEY and KITE_ACCESS_TOKEN.
This application does not scrape NSE, place orders or invent live quotes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import secrets
import threading
import time
from contextlib import asynccontextmanager
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sectors import ALL_SYMBOLS, PRIMARY_SECTORS, SECTOR_DEFINITIONS, SYMBOL_TO_SECTOR
from history_intelligence import live_features
from fresh_momentum import fresh_momentum, update_minute_tape
from momentum_rating import momentum_rating
from adaptive_ranking import adaptive_rank, sort_picks_by_ob_score
from stable_ob_picks import eligible_ob_pick, update_members
from orderblock_intelligence import analyze_order_block
from candle_profile import candle_volume_profile

ROOT = Path(__file__).parent
# Fingerprinted JS/CSS URLs plus no-store HTML prevent Render/CDN/browser serving
# older 01-06 leader cards after the MOM/OB update. The fingerprint changes
# automatically whenever either visible card asset changes on deployment.
UI_ASSET_VERSION = hashlib.sha256(
    (ROOT / 'static' / 'app.js').read_bytes() +
    (ROOT / 'static' / 'reference-terminal.css').read_bytes() +
    (ROOT / 'static' / 'top6-scores.css').read_bytes() +
    (ROOT / 'static' / 'tradingview-links.css').read_bytes()
).hexdigest()[:12]
NO_CACHE = {'Cache-Control': 'no-store, max-age=0, must-revalidate', 'Pragma': 'no-cache'}

class CriticalAssetStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in ('app.js', 'top6-scores.css', 'tradingview-links.css', 'reference-terminal.css'):
            response.headers.update(NO_CACHE)
        return response

load_dotenv(ROOT / '.env')  # server-side only; never returned by an API endpoint
IST = ZoneInfo('Asia/Kolkata')
MARKET_MODE = os.getenv('MARKET_MODE', 'demo').strip().lower()
if MARKET_MODE not in ('demo', 'live', 'kite'):
    raise ValueError('MARKET_MODE must be demo, live, or kite')
if MARKET_MODE == 'kite' and (not os.getenv('KITE_API_KEY') or not os.getenv('KITE_ACCESS_TOKEN')):
    raise RuntimeError('Kite mode requires KITE_API_KEY and KITE_ACCESS_TOKEN in .env or the server environment')
FEED_TOKEN = os.getenv('MARKET_FEED_TOKEN', '')
if MARKET_MODE == 'live' and len(FEED_TOKEN) < 12:
    raise RuntimeError('Set a secure MARKET_FEED_TOKEN (>=12 characters) in live mode')

# Demo reference values, purely fictional/recreated from the shared screenshots.
LEADER_LIMIT = 6  # Six adaptive live-momentum leaders appear on each side.

SHOWCASE = {
    'PGEL': (527.65, 8.19, 14.7),
    'MOTILALOFS': (824.25, 6.93, 10.8),
    'TIINDIA': (3290.0, 6.58, 12.6),
    'INOXWIND': (207.85, 5.92, 16.3),
    'NESTLEIND': (1334.45, 3.42, 6.4),
    'ASIANPAINT': (2424.20, 2.46, 11.2),
    'NHPC': (86.30, 1.54, 5.2),
    'HINDUNILVR': (2450.80, 3.09, 8.2),
    'DABUR': (387.20, 2.71, 6.2),
    'JINDALSTEL': (991.20, 3.79, 7.1),
    'LAURUSLABS': (756.30, 3.99, 10.1),
    'AMBER': (6544.70, 4.31, 8.7),
    'PHOENIXLTD': (1645.10, -2.66, 12.2),
    'COALINDIA': (378.95, -3.16, 9.8),
    'BANDHANBNK': (152.55, -2.45, 13.2),
    'PNBHOUSING': (905.30, -3.33, 11.4),
    'GRASIM': (2667.80, -3.10, 9.6),
    'MPHASIS': (3011.60, -3.04, 7.8),
    'TECHM': (1450.25, -2.78, 7.6),
}


def stable_seed(symbol: str) -> int:
    return int.from_bytes(hashlib.sha256(symbol.encode()).digest()[:8], 'big')


def r2(x: float) -> float:
    return round(float(x), 2)


def price_label(x: float) -> str:
    return str(round(x, 2))


def candle_vwap(candles: list[dict]) -> list[dict]:
    cumulative_pv = 0.0
    cumulative_v = 0.0
    result = []
    last_session = None
    for c in candles:
        session = str(c.get('time', ''))[:10]
        if session != last_session:
            cumulative_pv = cumulative_v = 0.0
            last_session = session
        volume = float(c.get('volume', 0))
        typical = (c['high'] + c['low'] + c['close']) / 3
        cumulative_v += volume
        cumulative_pv += typical * volume
        result.append({**c, 'vwap': r2(cumulative_pv / cumulative_v if cumulative_v else c['close'])})
    return result


# 60-minute candles are aligned to the NSE session start (09:15 IST), not clock-hour boundaries.
SUPPORTED_TIMEFRAMES = {'5m': 5, '15m': 15, '30m': 30, '1h': 60}


def resample_candles(candles: list[dict], timeframe: str) -> list[dict]:
    """Aggregate stored 5-minute NSE candles to a requested timeframe.

    Grouping uses session-open-relative timestamps, and never combines trading days.
    Preserve the final constituent 5-minute candle's session VWAP (rather than
    approximating it again from a larger candle's OHLC typical price).
    The last bucket may be partial while the exchange is trading.
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f'Unsupported timeframe: {timeframe}')
    if timeframe == '5m':
        return [dict(c) for c in candles]
    minutes = SUPPORTED_TIMEFRAMES[timeframe]
    output = []
    for c in candles:
        stamp = datetime.fromisoformat(c['time'])
        local = stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)
        session_open = local.replace(hour=9, minute=15, second=0, microsecond=0)
        elapsed = (local - session_open).total_seconds() // 60
        bucket_time = session_open + timedelta(minutes=(int(elapsed) // minutes) * minutes)
        key = bucket_time.isoformat()
        if output and output[-1]['time'] == key:
            bar = output[-1]
            bar['high'] = max(bar['high'], c['high'])
            bar['low'] = min(bar['low'], c['low'])
            bar['close'] = c['close']
            bar['volume'] += int(c.get('volume', 0))
            bar['vwap'] = c.get('vwap', bar['vwap'])
        else:
            output.append({'time': key, 'open': c['open'], 'high': c['high'],
                           'low': c['low'], 'close': c['close'],
                           'volume': int(c.get('volume', 0)),
                           'vwap': c.get('vwap', c['close'])})
    return output


def find_order_blocks(candles: list[dict]) -> list[dict]:
    """Price-action approximation, NOT order-book depth or confirmed institutional orders.

    An opposing candle followed within 1-3 bars by an impulse and local structure break
    is a candidate. The zone uses the opposing candle's range; show recent zones only.
    """
    if len(candles) < 10:
        return []
    ranges = [max(c['high'] - c['low'], c['close'] * 0.0002) for c in candles]
    blocks = []
    for i in range(5, len(candles) - 3):
        c = candles[i]
        rolling = sum(ranges[max(0, i - 8):i]) / len(ranges[max(0, i - 8):i])
        nxt = candles[i + 1:i + 4]
        prior = candles[i - 4:i]
        if c['close'] < c['open']:
            peak = max(k['high'] for k in nxt)
            if peak > max(k['high'] for k in prior) and peak - c['close'] > 1.4 * rolling:
                blocks.append({'type': 'bullish', 'fromIndex': i, 'toIndex': len(candles) - 1,
                               'low': r2(c['low']), 'high': r2(c['open']),
                               'strength': min(99, round(65 + 12 * (peak - c['close']) / max(rolling, .01)))})
        elif c['close'] > c['open']:
            trough = min(k['low'] for k in nxt)
            if trough < min(k['low'] for k in prior) and c['close'] - trough > 1.4 * rolling:
                blocks.append({'type': 'bearish', 'fromIndex': i, 'toIndex': len(candles) - 1,
                               'low': r2(c['open']), 'high': r2(c['high']),
                               'strength': min(99, round(65 + 12 * (c['close'] - trough) / max(rolling, .01)))})
    # Avoid overlapping labels: keep latest 3 per side, max 5 visible.
    return sorted((b for side in ['bullish', 'bearish'] for b in [x for x in blocks if x['type'] == side][-3:]),
                  key=lambda b: b['fromIndex'])[-5:]


class Scanner:
    def __init__(self, demo: bool):
        self.demo = demo
        self.lock = threading.RLock()
        self.feed_status = 'demo' if demo else 'waiting'
        self.feed_error = ''
        self.last_quote_at = None
        self.missing_symbols = []
        self.mapped_futures = 0
        self.records: dict[str, dict] = {}
        self.clients: set[asyncio.Queue] = set()
        self.revision = 0
        self.paused = False
        self.tick_count = 0  # CASH tick events this IST trading session (no bootstrap / FUT OI)
        self.session_date = None
        self.baselines: dict[str, dict] = {}
        self.history_error = ''
        self.history_loading = False
        self.history_complete = 0
        self.history_failed = 0
        self.chart_lru = OrderedDict()  # only a few charts retain long history in RAM
        self.leader_ob_cache = {}  # (symbol -> time, latest-bar key, status, 5m OB payload)
        # Top 3 memberships are sticky but may change after a challenger wins
        # across three consecutive distinct market-data revisions.
        self.top3_lock_date = None
        self.top3_locked = {'bullish': [], 'bearish': []}  # current member identities
        self.top3_pending = {'bullish': None, 'bearish': None}
        self.top3_last_checked_revision = {'bullish': None, 'bearish': None}
        self.top3_last_rows = {}  # Fallback display if a selected record disappears
        self.max_chart_symbols = max(1, min(5, int(os.getenv('MAX_CHART_SYMBOLS', '2'))))
        if demo:
            for symbol in ALL_SYMBOLS:
                self.records[symbol] = self._make_demo_record(symbol)

    def _make_demo_record(self, symbol: str) -> dict:
        rng = random.Random(stable_seed(symbol))
        if symbol in SHOWCASE:
            last, change, oi_change = SHOWCASE[symbol]
        else:
            last = round(rng.uniform(75, 3300) * (0.23 if rng.random() < 0.23 else 1), 2)
            sign = 1 if rng.random() < 0.68 else -1
            change = round(sign * rng.uniform(0.18, 2.52), 2)
            oi_change = round(rng.uniform(-7, 19), 2)
        prev_close = last / (1 + change / 100)
        start = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        candles = []
        length = 68
        baseline_vol = rng.randint(28000, 170000)
        current = prev_close * (1 + rng.uniform(-0.002, 0.002))
        for i in range(length):
            f = (i + 1) / length
            # Trend plus deterministic local fluctuations; final close is exactly the showcase price.
            expected = prev_close + (last - prev_close) * f
            target = last if i == length - 1 else expected + prev_close * rng.gauss(0, 0.0027)
            o = current
            c = max(.05, target)
            spread = prev_close * rng.uniform(0.001, .0038)
            high = max(o, c) + spread * rng.uniform(.2, .85)
            low = min(o, c) - spread * rng.uniform(.2, .85)
            candles.append({'time': (start + timedelta(minutes=5*i)).isoformat(), 'open': r2(o),
                            'high': r2(high), 'low': r2(max(.01, low)), 'close': r2(c),
                            'volume': int(baseline_vol * rng.uniform(.6, 2.6))})
            current = c
        candles = candle_vwap(candles)
        initial_oi = rng.randint(350000, 8500000)
        oi = int(initial_oi * (1 + oi_change / 100))
        relative_volume = r2(rng.uniform(1.05, 3.45))
        return {'symbol': symbol, 'sector': SYMBOL_TO_SECTOR[symbol], 'price': r2(last),
                'prevClose': r2(prev_close), 'open': r2(candles[0]['open']),
                'change': r2(change), 'oi': oi, 'oiBase': initial_oi,
                'oiChange': r2(oi_change), 'volume': sum(c['volume'] for c in candles),
                'rvol': relative_volume, 'candles': candles,
                'updated': datetime.now(IST).isoformat()}

    @staticmethod
    def direction(rec: dict) -> str:
        return 'bullish' if rec['change'] >= 0 else 'bearish'

    def score(self, rec: dict) -> float:
        """Directional momentum + participation, NOT a liquidity-depth ranking."""
        strength = abs(rec['change'])
        rv = min(5., max(0., float(rec.get('rvol') or 0)))
        oi = min(24, max(-15, float(rec.get('oiChange') or 0)))
        vwap = rec.get('providerVwap') or (rec['candles'][-1]['vwap'] if rec['candles'] else rec['price'])
        price_vwap = rec['price'] - vwap
        aligned = (price_vwap >= 0) == (rec['change'] >= 0)
        baseline = self.baselines.get(rec['symbol'])
        if not self.demo and baseline and baseline.get('sessions', 0) >= 14:
            # Normalize momentum to its own previous-session daily range; do not
            # mistake a naturally volatile stock for unusual strength.
            features = live_features(baseline, rec['price'], rec['prevClose'], rec.get('volume'),
                                     datetime.fromisoformat(rec['updated']))
            unusual_move = min(5., features.get('rangeMultiple') or 0.)
            participation = min(4., features.get('rvol') or 0.)
            breakout = 7 if features.get('breakout20') else 0
            return round(unusual_move * 22 + participation * 9 + breakout +
                         (5 if aligned else 0) + max(0, oi) * .12, 4)
        # While an actual Kite 20-day baseline is loading, direction remains
        # available but the UI explicitly shows history pending / RVOL=N/A.
        return strength * 12 + min(rv, 4) * 6 + max(0, oi) * .3 + (5 if aligned else 0)

    def public_record(self, rec: dict) -> dict:
        if rec.get('oiChange') is None or rec.get('oi') is None:
            buildup = 'OI UNAVAILABLE'
        elif rec['oiChange'] == 0:
            buildup = 'OI UNCHANGED'
        elif rec['change'] > 0 and rec['oiChange'] > 0:
            buildup = 'LONG BUILD-UP'
        elif rec['change'] < 0 and rec['oiChange'] > 0:
            buildup = 'SHORT BUILD-UP'
        elif rec['change'] > 0:
            buildup = 'SHORT COVERING'
        else:
            buildup = 'LONG UNWINDING'
        vwap = rec.get('providerVwap') or (rec['candles'][-1]['vwap'] if rec['candles'] else None)
        indicators = live_features(self.baselines.get(rec['symbol']), rec['price'], rec['prevClose'],
                                   rec.get('volume'), datetime.fromisoformat(rec['updated'])) if not self.demo else {}
        if self.demo:
            # Demo trend curves are fictional and should not imply verified 5m/15m momentum.
            recent = {'momentumState':'DEMO', 'recent5mPct':None, 'recent15mPct':None,
                      'recent30mPct':None, 'recent60mPct':None,
                      'momentumWindowsReady':0, 'momentumWindowsTotal':4,
                      'recentVolumeRatio':None, 'freshScore':0.0, 'pickEligible':False,
                      'volumeConfirmed':False, 'freshReason':'Simulated data; not a live momentum signal'}
        else:
            recent = fresh_momentum(rec.get('minuteTape'), rec['price'], rec['change'],
                                    indicators.get('adr20'), (indicators.get('rvol') if MARKET_MODE == 'kite'
                                                              else rec.get('rvol')),
                                    datetime.now(IST))
        public = {'symbol': rec['symbol'], 'sector': rec['sector'], 'price': rec['price'],
                  'change': rec['change'], 'oi': rec['oi'], 'oiChange': rec['oiChange'],
                  'volume': rec['volume'],
                  'sessionMovePct': round(100 * (rec['price'] / rec.get('open', rec['price']) - 1), 3) if rec.get('open') else None,
                  'rvol': indicators.get('rvol') if (not self.demo and MARKET_MODE == 'kite') else rec.get('rvol'), 'vwap': vwap,
                  'buildup': buildup, 'direction': self.direction(rec), 'score': round(self.score(rec), 2),
                  'spark': [c['close'] for c in rec['candles'][-22:]],
                  'oiSource': rec.get('oiSource', 'simulated' if self.demo else 'unavailable'),
                  **indicators, **recent}
        public.update(momentum_rating(public, demo=self.demo))
        public['adaptiveRankScore'] = adaptive_rank(public, demo=self.demo)
        return public

    def order_block_signal(self, rec: dict, timeframe: str = '5m', candles=None) -> dict:
        """Bounded closed-bar analysis. Runs only for picks and opened charts.

        No full-universe historical candle expansion and no extra Kite requests.
        """
        source = rec.get('candles', [])
        if candles is None:
            candles = resample_candles(source[-140:], timeframe)[-90:]
        fifteen = resample_candles(source[-160:], '15m')[-90:]
        baseline = self.baselines.get(rec['symbol'])
        volume_features = live_features(baseline, rec['price'], rec['prevClose'],
                                        rec.get('volume'), datetime.fromisoformat(rec['updated'])) if baseline else {}
        result = analyze_order_block(candles, timeframe=timeframe,
                                     candles15=fifteen,
                                     rvol20=volume_features.get('rvol'), demo=self.demo)
        # A historical chart pattern is not an actionable live signal if the
        # WebSocket feed is stale, disconnected, or market is closed.
        if not self.demo and result['obState'] == 'READY' and self.market_status() != 'LIVE':
            result['obState'] = 'WAIT'
            result['obReason'] = 'Waiting for a LIVE Kite feed and fresh market quotes'
        return result

    def cached_leader_ob(self, rec: dict, market_status: str) -> dict:
        """Reuse bounded 5m candle-based OB analysis across rapid SSE snapshots.

        Top-6 displays need 12 scores instead of only the Top-3's six. The
        dashboard polls much faster than a five-minute candle can complete;
        caching for <=25 seconds avoids repeatedly scanning the same bars on
        Render Free. No history API calls or new subscriptions are made.
        """
        symbol = rec['symbol']
        candles = rec.get('candles') or []
        tail = candles[-1] if candles else {}
        # The session date and newest bar identity prevent stale reuse at a
        # restart/session rollover. We still refresh during the bar for the
        # countdown to complete-candle eligibility and retest freshness.
        key = (self.session_date, len(candles), tail.get('time'), market_status)
        now = time.monotonic()
        entry = self.leader_ob_cache.get(symbol)
        if entry and entry[1] == key and now - entry[0] < 25:
            return dict(entry[2])
        result = self.order_block_signal(rec)
        self.leader_ob_cache[symbol] = (now, key, dict(result))
        if len(self.leader_ob_cache) > 35:
            # Evict stale stocks if intraday leadership changes repeatedly.
            old = sorted(self.leader_ob_cache, key=lambda k: self.leader_ob_cache[k][0])
            for stale in old[:-24]:
                self.leader_ob_cache.pop(stale, None)
        return result

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_unlocked()

    def market_status(self):
        now = datetime.now(IST)
        scheduled = now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)
        if not scheduled:
            return 'CLOSED'
        if self.demo:
            return 'DEMO'
        if not self.last_quote_at or self.feed_status != 'connected' or self.tick_count < 1:
            return 'WAITING'
        try:
            age = (now - datetime.fromisoformat(self.last_quote_at).astimezone(IST)).total_seconds()
        except (ValueError, TypeError):
            return 'WAITING'
        # NSE holidays / gaps should never be shown as LIVE just from weekday/time.
        return 'LIVE' if -20 <= age <= 120 else 'STALE'

    def set_baseline(self, symbol: str, baseline: dict):
        with self.lock:
            self.baselines[symbol] = baseline
            self.history_complete = len(self.baselines)
            if symbol in self.records:
                self.records[symbol]['rvol'] = None  # computed from historical profile

    def _snapshot_unlocked(self) -> dict:
        # NIFTY_50 is a cross-cutting list and not double-counted as a sector.
        all_rows = [self.public_record(rec) for rec in self.records.values()]
        # Rank CURRENT intraday opportunity, not a frozen opening spike.
        # The rolling 5m/15m/30m/1h score dominates; session-to-date return contributes
        # at most eight points. Historical baseline still informs RVOL/ATR.
        bull = sorted((r for r in all_rows if r['change'] >= 0),
                      key=lambda r: (r['adaptiveRankScore'], r['score']), reverse=True)
        bear = sorted((r for r in all_rows if r['change'] < 0),
                      key=lambda r: (r['adaptiveRankScore'], r['score']), reverse=True)
        for direction_rows in (bull, bear):
            for i, record in enumerate(direction_rows):
                record['rank'] = i + 1
                record['of'] = len(direction_rows)
        sectors = []
        for name, symbols in PRIMARY_SECTORS.items():
            included = [self.records[s] for s in symbols if s in self.records]
            if included:
                change = sum(x['change'] for x in included) / len(included)
                bullish = sum(x['change'] >= 0 for x in included)
                bearish = len(included) - bullish
            else:
                change, bullish, bearish = 0, 0, 0
            sectors.append({'name': name, 'change': r2(change), 'bullish': bullish,
                            'bearish': bearish, 'total': len(included)})
        sectors.sort(key=lambda s: s['change'], reverse=True)

        result = {'mode': MARKET_MODE if not self.demo else 'demo', 'revision': self.revision,
                  'feedStatus': self.feed_status, 'feedError': self.feed_error,
                  'lastQuoteAt': self.last_quote_at, 'missingSymbols': self.missing_symbols,
                  'futuresMapped': self.mapped_futures,
                  'tickCount': self.tick_count, 'sessionDate': self.session_date,
                  'marketStatus': self.market_status(), 'historyLoaded': len(self.baselines),
                  'historyLoading': self.history_loading, 'historyFailed': self.history_failed,
                  'historyError': self.history_error, 'historyTarget': len(ALL_SYMBOLS) if self.demo else len(ALL_SYMBOLS)-len(self.missing_symbols),
                  'asOf': datetime.now(IST).isoformat(), 'universeCount': len(ALL_SYMBOLS),
                  'trackedCount': len(all_rows), 'bullishCount': len(bull), 'bearishCount': len(bear),
                  'sectorCount': len(PRIMARY_SECTORS), 'sectors': sectors,
                  'bullish': bull[:LEADER_LIMIT], 'bearish': bear[:LEADER_LIMIT],
                  'paused': self.paused}
        # Compute lightweight bar profiles only for visible leaders, never for
        # all 198 symbols on every SSE snapshot (Render Free CPU constraint).
        # Attach the same explicit, completed-candle OB data to every visible
        # Top-6 leader that was previously computed only for Top-3 picks.
        # Compute each symbol once, then reuse the same result on both cards.
        market_status = result['marketStatus']
        for direction in ('bullish', 'bearish'):
            for leader in result[direction]:
                record = self.records.get(leader['symbol'])
                if record:
                    leader['candleProfile'] = candle_volume_profile(record.get('candles', []))
                    leader.update(self.cached_leader_ob(record, market_status))
        # Stable *dynamic* Top 3. Eligible Top 6 candidates may replace
        # weaker members after three distinct market-data updates; neither an
        # OB WAIT score alone nor HTTP polling can force a replacement.
        session_key = self.session_date or datetime.now(IST).date().isoformat()
        if session_key != self.top3_lock_date:
            self.top3_lock_date = session_key
            self.top3_locked = {'bullish': [], 'bearish': []}
            self.top3_pending = {'bullish': None, 'bearish': None}
            self.top3_last_checked_revision = {'bullish': None, 'bearish': None}
            self.top3_last_rows.clear()
        all_by_symbol = {row['symbol']: row for row in all_rows}
        market_live = market_status == 'LIVE'
        for direction in ('bullish', 'bearish'):
            leaders = result[direction]
            ranked = sort_picks_by_ob_score([
                row for row in leaders if eligible_ob_pick(
                    row, direction, market_live=market_live, demo=self.demo)
            ])
            # Include fresh 5m candle-derived OB analysis for current members
            # that have left the Top 6. They must remain visible until a valid
            # challenger has proved higher quality.
            current = {row['symbol']: row for row in leaders}
            for symbol in self.top3_locked[direction]:
                if symbol in current:
                    continue
                rec = self.records.get(symbol)
                if rec is not None:
                    row = dict(all_by_symbol[symbol])
                    row['candleProfile'] = candle_volume_profile(rec.get('candles', []))
                    row.update(self.cached_leader_ob(rec, market_status))
                    current[symbol] = row
            # Freeze membership on an unavailable feed, but keep rendering.
            if self.demo or market_live:
                members, pending, checked = update_members(
                    self.top3_locked[direction], self.top3_pending[direction],
                    ranked, current, revision=self.revision,
                    last_revision=self.top3_last_checked_revision[direction],
                    direction=direction, market_live=market_live, demo=self.demo)
                self.top3_locked[direction] = members
                self.top3_pending[direction] = pending
                self.top3_last_checked_revision[direction] = checked
            else:
                self.top3_pending[direction] = None
            picks = []
            for symbol in self.top3_locked[direction]:
                row = current.get(symbol)
                if row is None:
                    old = self.top3_last_rows.get(symbol)
                    if old is None:
                        continue
                    row = {**old, 'pickDataStale': True, 'pickEligible': False,
                           'obQualityScore': None, 'obState': 'WAIT',
                           'obReason': 'Market record temporarily missing; no current OB score'}
                item = dict(row)
                item['pickPinned'] = True  # Stable member, not permanently locked
                same_side = (item.get('change', 0) >= 0) == (direction == 'bullish')
                item['pickDirectionChanged'] = not same_side
                confirmed = (bool(item.get('pickEligible')) and market_live and not self.demo
                             and same_side and item.get('obState') == 'READY'
                             and not item.get('pickDataStale'))
                item['pickConfirmed'] = confirmed
                item['pickStatus'] = ('DATA WAIT' if item.get('pickDataStale') else
                                      'SIDE CHANGED' if not same_side else
                                      'DEMO' if self.demo else 'ACTIVE' if confirmed else
                                      'FEED WAIT' if item.get('pickEligible') and not market_live else
                                      'WATCH · OB' if item.get('pickEligible') and item.get('obState') == 'WAIT' else
                                      {'ACTIVE': 'WATCH · RVOL',
                                       'CONSOLIDATING': 'SIDEWAYS',
                                       'FADING': 'FADING',
                                       'WARMING_UP': 'WARMUP',
                                       'STALE': 'STALE'}.get(item.get('momentumState'), 'WATCH'))
                self.top3_last_rows[symbol] = dict(item)
                picks.append(item)
            # The same members can change display positions as OB scores
            # update; a missing current score goes last, never reuses history.
            picks.sort(key=lambda row: (
                row['obQualityScore'] if isinstance(row.get('obQualityScore'), (int, float))
                and math.isfinite(row['obQualityScore']) else float('-inf')
            ), reverse=True)
            result[direction + 'Picks'] = picks
        result['top3Locked'] = {direction: bool(self.top3_locked[direction])
                                for direction in ('bullish', 'bearish')}  # Legacy API
        result['top3Pending'] = self.top3_pending.copy()
        result['top3Strategy'] = 'STABLE_DYNAMIC_OB'
        return result

    def details(self, symbol: str, timeframe: str = '5m') -> dict:
        with self.lock:
            return self._details_unlocked(symbol, timeframe)

    def _details_unlocked(self, symbol: str, timeframe: str = '5m') -> dict:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise HTTPException(400, 'Allowed timeframes: 5m, 15m, 30m, 1h')
        if symbol not in ALL_SYMBOLS:
            raise HTTPException(404, 'Symbol is not part of the configured universe')
        rec = self.records.get(symbol)
        if rec is None:
            return {'symbol': symbol, 'unavailable': True, 'sector': SYMBOL_TO_SECTOR[symbol], 'timeframe': timeframe, 'candles': []}
        public = self.public_record(rec)
        direction_rows = sorted((x for x in self.records.values() if self.direction(x) == self.direction(rec)),
                                key=lambda x: (self.public_record(x)['adaptiveRankScore'], self.score(x)),
                                reverse=True)
        rank = next(i+1 for i,x in enumerate(direction_rows) if x['symbol'] == symbol)
        candles = resample_candles(rec['candles'], timeframe)[-90:]
        blocks = find_order_blocks(candles)
        ob_analysis = self.order_block_signal(rec, timeframe, candles)
        p = rec['price']
        if len(candles) >= 7:
            atr = sum(c['high'] - c['low'] for c in candles[-14:]) / len(candles[-14:])
        else:
            atr = p * .006
        atr = max(p * .0025, atr)
        if len(candles) < 14 and self.baselines.get(symbol, {}).get('atr14'):
            atr = self.baselines[symbol]['atr14'] * SUPPORTED_TIMEFRAMES[timeframe] / 375
        vwap = rec.get('providerVwap') or (candles[-1]['vwap'] if candles else p)
        bullish = rec['change'] >= 0
        support = r2(min(vwap, p - .65*atr) if bullish else min(vwap, p - 1.3*atr))
        resistance = r2(p + (1.15 if bullish else .5) * atr)
        resistance2 = r2(p + 2.25 * atr)
        downside1 = r2(p - 1.18 * atr)
        downside2 = r2(p - 2.24 * atr)
        return {**public, **ob_analysis, 'rank': rank, 'of': len(direction_rows),
                'prevClose': rec['prevClose'], 'open': rec['open'], 'candles': candles,
                'candleProfile': candle_volume_profile(rec.get('candles', [])),
                'blocks': blocks, 'levels': {'vwap': vwap, 'support': support, 'r1': resistance,
                'r2': resistance2, 's1': downside1, 's2': downside2},
                'timeframe': timeframe, 'updated': rec['updated'],
                'historySource': rec.get('historySource', 'synthetic' if self.demo else 'ticks'),
                'disclaimer': 'Order blocks, BOS, liquidity sweeps and FVG are candle-derived estimates, not verified institutional orders; delta is unavailable from Kite quotes.'}

    async def publish(self):
        self.revision += 1
        serialized = json.dumps(self.snapshot(), separators=(',', ':'))
        for client in list(self.clients):
            if client.full():
                try: client.get_nowait()
                except asyncio.QueueEmpty: pass
            client.put_nowait(serialized)

    async def update_demo(self):
        if not self.demo or self.paused:
            return
        session = datetime.now(IST).date().isoformat()
        if self.session_date != session:
            self.session_date, self.tick_count = session, 0
            # Demo produces another fictional session; not a market-data source.
            self.records = {symbol: self._make_demo_record(symbol) for symbol in ALL_SYMBOLS}
        for rec in self.records.values():
            rng = random.Random(stable_seed(rec['symbol']) + self.revision * 8191)
            p = rec['price']
            delta = p * rng.uniform(-0.00042, 0.00042)
            next_price = r2(max(.05, p + delta))
            c = rec['candles'][-1]
            c['high'] = r2(max(c['high'], next_price))
            c['low'] = r2(min(c['low'], next_price))
            c['close'] = next_price
            addvol = rng.randint(120, 5000)
            c['volume'] += addvol
            # Recompute session VWAP after mutating trailing candle.
            rec['candles'][-1]['vwap'] = candle_vwap(rec['candles'])[-1]['vwap']
            rec['price'] = next_price
            rec['volume'] += addvol
            rec['change'] = r2((next_price / rec['prevClose'] - 1) * 100)
            rec['updated'] = datetime.now(IST).isoformat()
            self.tick_count += 1
        await self.publish()

    def ingest_quote(self, incoming: dict):
        with self.lock:
            return self._ingest_quote(incoming)

    def _ingest_quote(self, incoming: dict):
        symbol = incoming['symbol'].strip().upper()
        if symbol not in ALL_SYMBOLS:
            raise ValueError(f'Unknown NSE symbol: {symbol}')
        price = float(incoming['price'])
        if price <= 0 or not math.isfinite(price):
            raise ValueError('price must be a positive finite number')
        old = self.records.get(symbol)
        prev_close = float(incoming.get('prev_close') or (old['prevClose'] if old else price))
        if prev_close <= 0:
            raise ValueError('prev_close must be positive')
        now = datetime.now(IST)
        if incoming.get('timestamp'):
            at = datetime.fromisoformat(incoming['timestamp'].replace('Z', '+00:00'))
            if at.tzinfo is None:
                raise ValueError('timestamp requires timezone offset or Z')
            now = at.astimezone(IST)
        bin_minute = 5 * (now.minute // 5)
        bucket = now.replace(minute=bin_minute, second=0, microsecond=0)
        candles = old['candles'] if old else []
        # Chart may retain historical candles, but the per-day VWAP and cumulative
        # volume are reset by date inside candle_vwap / ingest_kite_cash.
        vol_increment = max(0, int(incoming.get('volume_increment') or 0))
        if candles and datetime.fromisoformat(candles[-1]['time']) > bucket:
            return  # Ignore out-of-order or stale packets that would corrupt candles.
        if candles and datetime.fromisoformat(candles[-1]['time']) == bucket:
            last = candles[-1]
            last['close'] = r2(price)
            last['high'] = r2(max(price, last['high']))
            last['low'] = r2(min(price, last['low']))
            last['volume'] += vol_increment
        else:
            candles.append({'time': bucket.isoformat(), 'open': r2(price), 'high': r2(price),
                            'low': r2(price), 'close': r2(price), 'volume': vol_increment})
        # Most stocks carry only the current session. Recalculating VWAP over
        # ~950 historical bars on *every tick* wastes CPU; previously completed
        # sessions already have immutable VWAP values.
        keep = 950 if symbol in self.chart_lru else 90
        candles = candles[-keep:]
        today_key = bucket.date().isoformat()
        first_today = len(candles)
        while first_today > 0 and candles[first_today - 1]['time'][:10] == today_key:
            first_today -= 1
        candles[first_today:] = candle_vwap(candles[first_today:])
        oi_value = incoming.get('oi', old.get('oi') if old else None)
        oi = int(oi_value) if oi_value is not None else None
        base_value = incoming.get('oi_base', old.get('oiBase') if old else None)
        oi_base = int(base_value) if base_value is not None else oi
        oi_change = r2((oi / oi_base - 1) * 100) if oi is not None and oi_base else None
        supplied_volume = incoming.get('volume')
        total_volume = int(supplied_volume if supplied_volume is not None else ((old['volume'] if old else 0) + vol_increment))
        self.records[symbol] = {'symbol': symbol, 'sector': SYMBOL_TO_SECTOR[symbol],
                                'price': r2(price), 'prevClose': r2(prev_close),
                                'open': r2(incoming['open']) if incoming.get('open') else (old['open'] if old else r2(price)),
                                'change': r2((price / prev_close - 1)*100), 'oi': oi, 'oiBase': oi_base,
                                'oiChange': oi_change, 'volume': total_volume,
                                'rvol': float(incoming['rvol']) if incoming.get('rvol') is not None else None,
                                'providerVwap': incoming.get('average_price') or (old.get('providerVwap') if old else None),
                                'oiSource': incoming.get('oi_source') or (old.get('oiSource') if old else 'unavailable'),
                                'historySource': old.get('historySource', 'ticks') if old else 'ticks',
                                'cumVolumeAt': old.get('cumVolumeAt') if old else None,
                                'minuteTape': update_minute_tape(old.get('minuteTape') if old else None,
                                                                 now, price, total_volume),
                                'candles': candles, 'updated': now.isoformat()}
        self.last_quote_at = now.isoformat()


    def ingest_kite_cash(self, payload: dict, bootstrap=False):
        """Ingest a real NSE CASH quote; derive volume deltas from daily cumulative volume.

        Bootstrap is not a fresh tick. Never label old quote timestamps as LIVE.
        """
        with self.lock:
            symbol = payload['symbol']
            previous = self.records.get(symbol)
            timestamp = datetime.fromisoformat(payload['timestamp']).astimezone(IST)
            cumulative = payload.get('volume')
            before = previous.get('cumVolumeAt') if previous else None
            previous_date = str(previous.get('updated', ''))[:10] if previous else None
            if previous_date != timestamp.date().isoformat():
                before = None  # New session; do not subtract yesterday's volume.
            diff = max(0, cumulative - before) if (cumulative is not None and before is not None and cumulative >= before) else 0
            if previous and datetime.fromisoformat(previous['updated']) > timestamp:
                return
            # Begin a fresh session only on non-bootstrap NSE quote events.
            # Reset event counter at rollover; rolling historical baselines remain.
            if not bootstrap and self.session_date != timestamp.date().isoformat():
                self.session_date = timestamp.date().isoformat()
                self.tick_count = 0
                # Reset session-specific volume and OI baselines, never the
                # rolling 20 previous trading sessions (those roll forward).
                for record in self.records.values():
                    record['oiBase'] = None
                    record['oiChange'] = None
            self._ingest_quote({**payload, 'volume_increment': diff})
            if not bootstrap:
                self.tick_count += 1
            if symbol in self.records:
                self.records[symbol]['cumVolumeAt'] = cumulative
                # Avoid suggesting that a stale bootstrap quote is a new trade.
                if bootstrap:
                    self.records[symbol]['historySource'] = 'quote snapshot'

    def set_futures_oi(self, symbol: str, oi: int):
        """Attach NFO futures OI to its NSE cash ticker; compare vs first observed OI.

        This is *not* previous-day OI and should not be called a day OI change.
        """
        with self.lock:
            rec = self.records.get(symbol)
            if rec is None or oi < 0:
                return
            baseline = rec.get('oiBase')
            if baseline is None:
                baseline = oi
            rec['oi'] = oi
            rec['oiBase'] = baseline
            rec['oiChange'] = r2((oi / baseline - 1) * 100) if baseline else None
            rec['oiSource'] = 'NFO FUT (since connect)'

    def seed_candles(self, symbol: str, history: list[dict]):
        """Merge Kite historical 5m bars with fresher locally received ticks."""
        with self.lock:
            rec = self.records.get(symbol)
            if not rec:
                return
            history_map = {}
            for c in history:
                stamp = c.get('date')
                if not isinstance(stamp, datetime):
                    continue
                stamp = stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)
                entry = {'time': stamp.isoformat(), 'open': r2(c['open']),
                         'high': r2(c['high']), 'low': r2(c['low']),
                         'close': r2(c['close']), 'volume': int(c['volume'])}
                history_map[entry['time']] = entry
            # Local ticks are more recent; preserve locally updated current bucket.
            today = datetime.now(IST).date()
            for c in rec['candles']:
                at = datetime.fromisoformat(c['time']).astimezone(IST)
                if at.date() == today and at.replace(minute=5*(at.minute//5),second=0,microsecond=0) >= datetime.now(IST).replace(minute=5*(datetime.now(IST).minute//5),second=0,microsecond=0):
                    history_map[c['time']] = c
            if history_map:
                rec['candles'] = candle_vwap([history_map[t] for t in sorted(history_map)][-950:])
                rec['historySource'] = 'Kite historical 5m + stream'
                self.chart_lru.pop(symbol, None)
                self.chart_lru[symbol] = True
                while len(self.chart_lru) > self.max_chart_symbols:
                    evicted, _ = self.chart_lru.popitem(last=False)
                    older = self.records.get(evicted)
                    if older:
                        older['candles'] = older['candles'][-90:]
                        older['historySource'] = 'Kite stream + compact chart cache'


market = Scanner(demo=MARKET_MODE == 'demo')
kite_feed = None
if MARKET_MODE == 'kite':
    from kite_feed import KiteFeed
    kite_feed = KiteFeed(market, os.environ['KITE_API_KEY'], os.environ['KITE_ACCESS_TOKEN'])


async def kite_publish_loop():
    while True:
        await asyncio.sleep(2)
        await market.publish()  # Throttled: never SSE-broadcast individual market ticks.


async def demo_loop():
    while True:
        await asyncio.sleep(4)
        await market.update_demo()


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if market.demo:
        tasks.append(asyncio.create_task(demo_loop()))
    if kite_feed is not None:
        tasks += [asyncio.create_task(kite_feed.run()), asyncio.create_task(kite_publish_loop())]
    yield
    if kite_feed is not None:
        kite_feed.close()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title='OrderFlow Studio API', version='2.0.0', lifespan=lifespan)
app.mount('/static', CriticalAssetStaticFiles(directory=ROOT/'static'), name='static')


@app.get('/')
def index():
    html = (ROOT/'static'/'index.html').read_text(encoding='utf-8')
    return HTMLResponse(html.replace('__ASSET_VERSION__', UI_ASSET_VERSION), headers=NO_CACHE)


@app.get('/api/version')
def api_version():
    # Public read-only deployment diagnostic; never includes API credentials.
    return {'uiBuild': UI_ASSET_VERSION,
            'top6CardHeader': 'MOM + OB', 'legacyRankBadges': False,
            'liveChartFollow': 'AUTO', 'tradingView5mLinks': True}



@app.get('/api/state')
def api_state():
    return market.snapshot()


@app.get('/api/symbol/{symbol}')
async def api_symbol(symbol: str, timeframe: str = '5m'):
    symbol = symbol.upper()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(400, 'Allowed timeframes: 5m, 15m, 30m, 1h')
    if symbol not in ALL_SYMBOLS:
        raise HTTPException(404, 'Symbol is not in this universe')
    if kite_feed is not None:
        await asyncio.to_thread(kite_feed.hydrate_history, symbol)
    return market.details(symbol, timeframe)


@app.get('/api/universe')
def api_universe():
    return {'symbols': ALL_SYMBOLS, 'sectors': SECTOR_DEFINITIONS, 'primarySectors': list(PRIMARY_SECTORS)}


@app.get('/api/events')
async def events():
    async def stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        market.clients.add(queue)
        try:
            yield 'retry: 3000\n\n'
            yield f'data: {json.dumps(market.snapshot(), separators=(",", ":"))}\n\n'
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f'data: {message}\n\n'
                except asyncio.TimeoutError:
                    yield ': ping\n\n'
        finally:
            market.clients.discard(queue)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache',
                                                                               'X-Accel-Buffering': 'no'})


class QuoteIn(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    prev_close: Optional[float] = Field(default=None, gt=0)
    timestamp: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0)
    volume_increment: int = Field(default=0, ge=0)
    oi: Optional[int] = Field(default=None, ge=0)
    oi_base: Optional[int] = Field(default=None, ge=0)
    rvol: Optional[float] = Field(default=None, gt=0)


class BatchIn(BaseModel):
    quotes: list[QuoteIn] = Field(min_length=1, max_length=1000)


@app.post('/api/ingest')
async def ingest(payload: BatchIn, x_feed_token: str = Header(default='')):
    if MARKET_MODE != 'live':
        raise HTTPException(403, 'POST /api/ingest is only available in MARKET_MODE=live')
    if not secrets.compare_digest(x_feed_token, FEED_TOKEN):
        raise HTTPException(401, 'Invalid feed token')
    errors = []
    accepted = 0
    for item in payload.quotes:
        try:
            market.ingest_quote(item.model_dump(exclude_none=True))
            accepted += 1
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    if accepted:
        await market.publish()
    return {'accepted': accepted, 'errors': errors, 'revision': market.revision}


@app.get('/api/health')
def health():
    return {'ok': MARKET_MODE != 'kite' or market.feed_status == 'connected',
            'mode': MARKET_MODE, 'feedStatus': market.feed_status,
            'feedError': market.feed_error, 'lastQuoteAt': market.last_quote_at,
            'symbols': len(market.records), 'futuresMapped': market.mapped_futures,
            'unmappedCount': len(market.missing_symbols), 'revision': market.revision,
            'tickCount': market.tick_count, 'sessionDate': market.session_date,
            'marketStatus': market.market_status(), 'historyLoaded': len(market.baselines),
            'historyTarget': len(ALL_SYMBOLS)-len(market.missing_symbols),
            'historyLoading': market.history_loading, 'historyFailed': market.history_failed,
            'uiBuild': UI_ASSET_VERSION}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')))
