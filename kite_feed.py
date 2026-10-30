"""Zerodha Kite Connect adapter for OrderFlow Studio.

This adapter is DATA-ONLY: it does not place, modify, or cancel orders.
API credentials live in server-side environment variables, never browser code.
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sectors import ALL_SYMBOLS
from history_intelligence import load_cache, make_baseline, save_cache

LOG = logging.getLogger(__name__)
IST = ZoneInfo('Asia/Kolkata')


def as_ist(dt):
    if not isinstance(dt, datetime):
        return datetime.now(IST)
    # Kite's Python client sometimes returns naive exchange-local datetimes.
    return (dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST))


def select_instruments(nse_instruments, nfo_instruments, symbols=ALL_SYMBOLS, today=None):
    """Exact NSE-EQ mapping and nearest *unexpired* NFO future (if one exists).

    NIFTY_50 is already de-duplicated in ALL_SYMBOLS. Never guess missing mappings.
    An NSE cash quote must not be confused with an NFO futures price.
    """
    today = today or datetime.now(IST).date()
    wanted = set(symbols)
    cash = {}
    for item in nse_instruments:
        sym = item.get('tradingsymbol')
        token = item.get('instrument_token')
        if sym in wanted and token and item.get('instrument_type') == 'EQ' and item.get('exchange') == 'NSE':
            cash[sym] = int(token)
    futures = {}
    for item in nfo_instruments:
        sym = item.get('name')
        if sym not in cash or item.get('instrument_type') != 'FUT' or item.get('segment') != 'NFO-FUT':
            continue
        expiry = item.get('expiry')
        if isinstance(expiry, str):
            try: expiry = date.fromisoformat(expiry[:10])
            except ValueError: continue
        if isinstance(expiry, datetime): expiry = expiry.date()
        if not isinstance(expiry, date) or expiry < today or not item.get('instrument_token'):
            continue
        if sym not in futures or expiry < futures[sym][0]:
            futures[sym] = (expiry, int(item['instrument_token']))
    return cash, {sym: pair[1] for sym, pair in futures.items()}


def cash_payload(symbol, quote, timestamp=None):
    """Unify Kite REST /quote and KiteTicker MODE_QUOTE data."""
    last = quote.get('last_price')
    ohlc = quote.get('ohlc') or {}
    prev = ohlc.get('close')
    if not isinstance(last, (int, float)) or last <= 0:
        return None
    if not isinstance(prev, (int, float)) or prev <= 0:
        return None
    volume = quote.get('volume_traded', quote.get('volume'))
    at = timestamp or quote.get('exchange_timestamp') or quote.get('timestamp') or quote.get('last_trade_time')
    if not isinstance(at, datetime):
        return None  # Receipt time cannot establish exchange freshness.
    avp = quote.get('average_traded_price', quote.get('average_price'))
    return {'symbol': symbol, 'price': float(last), 'prev_close': float(prev),
            'open': ohlc.get('open'), 'volume': int(volume) if volume is not None else None,
            'average_price': float(avp) if avp else None, 'timestamp': as_ist(at).isoformat(),
            'timestamp_verified': True, 'received_at': datetime.now(IST).isoformat()}


class KiteFeed:
    """Single Kite WebSocket for all configured EQ symbols plus available FUT OI.

    Ticks are handled in the SDK's background thread and synchronized by Scanner.
    Snapshot SSE publishing is scheduled on the FastAPI asyncio loop (not per tick).
    """
    def __init__(self, scanner, api_key: str, access_token: str, kite_cls=None, ticker_cls=None):
        if not api_key or not access_token:
            raise ValueError('Set KITE_API_KEY and KITE_ACCESS_TOKEN on the server')
        if kite_cls is None or ticker_cls is None:
            try:
                from kiteconnect import KiteConnect, KiteTicker
            except ImportError as e:
                raise RuntimeError('Install the kiteconnect package: pip install -r requirements.txt') from e
            kite_cls = kite_cls or KiteConnect
            ticker_cls = ticker_cls or KiteTicker
        self.kite = kite_cls(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.ticker_cls = ticker_cls
        self.api_key = api_key
        self.access_token = access_token
        self.scanner = scanner
        self.cash: dict[str, int] = {}
        self.futures: dict[str, int] = {}
        self.reverse_cash: dict[int, str] = {}
        self.reverse_futures: dict[int, str] = {}
        self.ws = None
        self.last_changed = 0.0
        self.cache_lock = threading.RLock()
        self.history_timestamps: dict[str, float] = {}
        self.history_locks: dict[str, threading.Lock] = {}
        self.history_rate = threading.Lock()
        self.last_history_call = 0.0
        self.stop_requested = False
        # Render: set HISTORY_CACHE_DIR to the mounted persistent disk path, if any.
        # Local fallback is safe but ephemeral across Render deploys/restarts.
        self.cache_dir = Path(os.getenv('HISTORY_CACHE_DIR', str(Path(__file__).parent / '.cache' / 'history')))
        self.history_refresh_date = None
        self.history_task = None
        self.recovery_task = None
        self.connection_generation = 0
        self.signal_hydrated = {}
        self.history_failures: dict[str, int] = {}
        self.chart_locks: dict[str, threading.Lock] = {}
        self.chart_hydrated: dict[str, float] = {}
        self.chart_failure_until: dict[str, float] = {}
        # One historical REST call at a time, with a pause between symbols.
        self.history_call_spacing = max(0.43, float(os.getenv('HISTORY_CALL_SPACING', '0.55')))
        self.history_symbol_pause = max(0., float(os.getenv('HISTORY_SYMBOL_PAUSE', '0.20')))
        self.history_max_attempts = max(1, min(4, int(os.getenv('HISTORY_MAX_ATTEMPTS', '2'))))
        self.history_retry_delay = max(5., float(os.getenv('HISTORY_RETRY_DELAY', '90')))
        self.chart_cache_seconds = max(60., float(os.getenv('CHART_HISTORY_REFRESH_SECONDS', '600')))

    def status(self, status: str, error: str = ''):
        with self.scanner.lock:
            self.scanner.feed_status = status
            # Keep message generic: SDK exceptions may contain an authorization header.
            self.scanner.feed_error = error[:170]

    def bootstrap(self):
        self.status('loading')
        nse = self.kite.instruments('NSE')
        nfo = self.kite.instruments('NFO')
        self.cash, self.futures = select_instruments(nse, nfo)
        self.reverse_cash = {token: sym for sym, token in self.cash.items()}
        self.reverse_futures = {token: sym for sym, token in self.futures.items()}
        with self.scanner.lock:
            self.scanner.missing_symbols = sorted(set(ALL_SYMBOLS) - set(self.cash))
            self.scanner.mapped_futures = len(self.futures)
        if not self.cash:
            raise RuntimeError('No NSE-EQ symbols resolved; check Kite account/instruments')
        # Quote bootstrap populates the board even when the market is closed. No order API.
        names = ['NSE:' + s for s in self.cash]
        for i in range(0, len(names), 175):
            if i: time.sleep(1.1)  # quote endpoint is limited to ~1 req/sec
            response = self.kite.quote(names[i:i+175])
            for symbol_with_exchange, quote in response.items():
                symbol = symbol_with_exchange.removeprefix('NSE:')
                if symbol not in self.cash:
                    continue
                payload = cash_payload(symbol, quote)
                if payload:
                    self.scanner.ingest_kite_cash(payload, bootstrap=True)
        # One NFO quote batch establishes the OI baseline for this connection session.
        # IMPORTANT: baseline is not previous-day OI.
        if self.futures:
            time.sleep(1.1)
            futures_by_name = {f'NFO:{item.get("tradingsymbol")}': (item.get('name'), item.get('instrument_token'))
                               for item in nfo if int(item.get('instrument_token') or 0) in self.reverse_futures}
            keys = list(futures_by_name)
            for i in range(0, len(keys), 175):
                if i: time.sleep(1.1)
                response = self.kite.quote(keys[i:i+175])
                for key, quote in response.items():
                    pair = futures_by_name.get(key)
                    if not pair: continue
                    symbol = pair[0]
                    oi = quote.get('oi')
                    if oi is not None:
                        self.scanner.set_futures_oi(symbol, int(oi))
        self.status('connecting')
        LOG.info('Kite mapped %d NSE cash stocks, %d NFO futures', len(self.cash), len(self.futures))

    def start_websocket(self):
        self.ws = self.ticker_cls(self.api_key, self.access_token)

        def on_connect(ws, _response):
            self.connection_generation += 1
            self.signal_hydrated.clear()
            with self.scanner.lock:
                for rec in self.scanner.records.values():
                    rec['historyRecovered'] = False
                self.scanner.leader_ob_cache.clear()
            tokens = list(self.reverse_cash) + list(self.reverse_futures)
            ws.subscribe(tokens)
            if self.reverse_cash:
                ws.set_mode(ws.MODE_FULL, list(self.reverse_cash))
            if self.reverse_futures:
                ws.set_mode(ws.MODE_FULL, list(self.reverse_futures))
            self.status('connected')
            LOG.info('Kite socket connected for %d instruments', len(tokens))

        def on_ticks(_ws, ticks):
            for tick in ticks:
                token = tick.get('instrument_token')
                if token in self.reverse_cash:
                    payload = cash_payload(self.reverse_cash[token], tick)
                    if payload:
                        self.scanner.ingest_kite_cash(payload)
                elif token in self.reverse_futures and tick.get('oi') is not None:
                    self.scanner.set_futures_oi(self.reverse_futures[token], int(tick['oi']))
            if ticks: self.last_changed = time.monotonic()

        def on_close(_ws, _code, _reason):
            if not self.stop_requested: self.status('reconnecting')

        def on_error(_ws, _code, _reason):
            if not self.stop_requested: self.status('reconnecting', 'WebSocket disconnected; reconnecting')

        self.ws.on_connect = on_connect
        self.ws.on_ticks = on_ticks
        self.ws.on_close = on_close
        self.ws.on_error = on_error
        self.ws.connect(threaded=True)

    async def run(self):
        try:
            await asyncio.to_thread(self.bootstrap)
            if not self.stop_requested:
                await asyncio.to_thread(self.start_websocket)
                # Does not block the websocket. Cache-backed scans become ready
                # progressively (max 2.5 historical requests/second).
                self.history_task = asyncio.create_task(self.history_refresh_loop())
                self.recovery_task = asyncio.create_task(self.signal_recovery_loop())
        except Exception as exc:
            # Don't return raw SDK exception contents to the browser (could contain secrets).
            LOG.error('Kite startup failed (%s)', type(exc).__name__)
            self.status('error', f'Kite initialization failed ({type(exc).__name__}); check credentials, API plan and server logs')

    def rate_limited_history(self, token, start, end, interval):
        """Serialize Kite historical requests across chart and baseline workers.

        Shared session + memory peaks are better behaved with a strict single
        in-flight request. Keep below the official 3-per-second rate limit.
        """
        for attempt in range(3):
            try:
                with self.history_rate:
                    wait = max(0., self.last_history_call + self.history_call_spacing - time.monotonic())
                    if wait:
                        time.sleep(wait)
                    self.last_history_call = time.monotonic()
                    return self.kite.historical_data(token, start, end, interval, oi=False)
            except Exception as exc:
                code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
                transient = code in (429, 502, 503, 504) or 'RateLimit' in type(exc).__name__
                if transient and attempt < 2:
                    time.sleep(1.5 * (2 ** attempt))
                    continue
                raise

    def load_history_for_symbol(self, symbol: str, date_of_session=None):
        """Compute one symbol's prior-20-session baseline without caching raw bars.

        The websocket continues independently. A single symbol is fetched at a
        time by the history loop to limit RAM spikes and API pressure.
        """
        if symbol not in self.cash:
            return None
        target = date_of_session or datetime.now(IST).date()
        lock = self.history_locks.setdefault(symbol, threading.Lock())
        with lock:
            cached = load_cache(self.cache_dir, symbol, target)
            if cached:
                self.scanner.set_baseline(symbol, cached['baseline'])
                return {'baseline': cached['baseline']}
            end = datetime.combine(target, datetime.min.time(), tzinfo=IST)
            now = datetime.now(IST)
            if end > now:
                end = now
            start = (end - timedelta(days=48)).replace(hour=9, minute=15, second=0, microsecond=0)
            token = self.cash[symbol]
            # Small daily series first. For a newly listed stock without enough
            # completed days, avoid the heavyweight intraday fetch entirely.
            daily = self.rate_limited_history(token, start, end, 'day')
            if not daily:
                raise ValueError('No completed daily history')
            complete_days = {as_ist(c['date']).date() for c in daily if as_ist(c['date']).date() < target}
            if len(complete_days) < 10:
                baseline = make_baseline(daily, [], target)
            else:
                candles = self.rate_limited_history(token, start, end, '5minute')
                if not candles:
                    raise ValueError('No completed 5-minute history')
                baseline = make_baseline(daily, candles, target)
                del candles  # do not retain 1500+ bars per symbol in memory
            save_cache(self.cache_dir, symbol, target, baseline)
            self.scanner.set_baseline(symbol, baseline)
            return {'baseline': baseline}

    async def history_refresh_loop(self):
        """One cooperative, rate-limited loader; bounded retries, no hot loops.

        Restarts on each new IST weekday. Weekend histories are left as-is; no
        attempt is made to fake an exchange calendar. An ephemeral Render sleep
        or redeploy loses its local cache and requires another warm-up.
        """
        while not self.stop_requested:
            target = datetime.now(IST).date()
            if target.weekday() < 5 and target != self.history_refresh_date:
                self.history_refresh_date = target
                self.history_failures.clear()
                with self.scanner.lock:
                    self.scanner.baselines = {
                        s: b for s, b in self.scanner.baselines.items()
                        if b.get('targetDate') == target.isoformat()}
                    self.scanner.history_loading = True
                    self.scanner.history_failed = 0
                    self.scanner.history_error = ''
                try:
                    await self.populate_all_history(target)
                finally:
                    with self.scanner.lock:
                        self.scanner.history_loading = False
            await asyncio.sleep(45)

    async def populate_all_history(self, target):
        """Yield between symbols, retry transient failures without request storms."""
        # Rank *only for warm-up priority*: baseline calculations still cover
        # every mapped symbol, and scores are recomputed from real data later.
        with self.scanner.lock:
            candidates = [r for r in self.scanner.records.values() if r['symbol'] in self.cash]
        rising = sorted((r for r in candidates if r['change'] >= 0),
                        key=lambda r: abs(r['change']), reverse=True)[:15]
        falling = sorted((r for r in candidates if r['change'] < 0),
                         key=lambda r: abs(r['change']), reverse=True)[:15]
        urgent = [r['symbol'] for r in rising + falling]
        remaining = list(dict.fromkeys(urgent + list(self.cash)))
        while remaining and not self.stop_requested:
            # A session changed while the last batch was loading: stop and
            # let the next loop build an appropriate baseline for the new date.
            if datetime.now(IST).date() != target:
                return
            failed = []
            for symbol in remaining:
                if self.stop_requested or datetime.now(IST).date() != target:
                    return
                try:
                    await asyncio.to_thread(self.load_history_for_symbol, symbol, target)
                    self.history_failures.pop(symbol, None)
                except Exception as exc:
                    code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
                    if code == 403 or type(exc).__name__ in ('TokenException', 'PermissionException'):
                        # Stop the whole warm-up when access is unauthorized.
                        # Continuing through 198 symbols just wastes Kite calls.
                        with self.scanner.lock:
                            self.scanner.history_error = 'Kite token expired or historical data not permitted'
                            self.scanner.history_failed = len(self.cash) - len(self.scanner.baselines)
                        LOG.error('Kite 20D history unauthorized; warmup paused until next token/deploy')
                        return
                    tries = self.history_failures.get(symbol, 0) + 1
                    self.history_failures[symbol] = tries
                    LOG.warning('20D history failed for %s on attempt %d (%s)',
                                symbol, tries, type(exc).__name__)
                    with self.scanner.lock:
                        self.scanner.history_error = 'Some history unavailable; see server logs'
                    if tries < self.history_max_attempts:
                        failed.append(symbol)
                await asyncio.sleep(self.history_symbol_pause)
            with self.scanner.lock:
                self.scanner.history_failed = len([
                    s for s, count in self.history_failures.items()
                    if count >= self.history_max_attempts])
            remaining = failed
            if remaining:
                await asyncio.sleep(self.history_retry_delay)

    def hydrate_history(self, symbol):
        """Load chart bars only when opened, and at most every 10 minutes.

        The all-symbols 20D intelligence worker only keeps compact indicators;
        chart fetching is separate so the whole universe never retains heavy
        price series. Old chart histories are LRU-evicted by Scanner.
        """
        if symbol not in self.cash:
            return
        lock = self.chart_locks.setdefault(symbol, threading.Lock())
        with lock:
            if time.monotonic() < self.chart_failure_until.get(symbol, 0.):
                return
            cached_at = self.chart_hydrated.get(symbol, 0.)
            with self.scanner.lock:
                rec = self.scanner.records.get(symbol)
                has_history = bool(rec and symbol in self.scanner.chart_lru)
            if has_history and time.monotonic() - cached_at < self.chart_cache_seconds:
                return
            now = datetime.now(IST)
            # ~30 calendar days support 90 one-hour bars across many sessions.
            start = (now - timedelta(days=33)).replace(hour=9, minute=15,
                                                       second=0, microsecond=0)
            try:
                # Also cooldown empty responses; otherwise the frontend's
                # 15-second chart refresh could hammer a bad instrument.
                self.chart_hydrated[symbol] = time.monotonic()
                candles = self.rate_limited_history(self.cash[symbol], start, now, '5minute')
                if candles:
                    self.scanner.seed_candles(symbol, candles[-950:])
                    self.chart_failure_until.pop(symbol, None)
                else:
                    self.chart_failure_until[symbol] = time.monotonic() + 180.
            except Exception as exc:
                self.chart_failure_until[symbol] = time.monotonic() + 180.
                LOG.warning('Chart history unavailable for %s (%s)', symbol, type(exc).__name__)

    def checkpoint_session(self):
        """Atomic checkpoint of observations only; no credentials or fabricated quotes."""
        with self.scanner.lock:
            payload = {'date': datetime.now(IST).date().isoformat(),
                       'tapes': {s: r.get('minuteTape', []) for s, r in self.scanner.records.items()}}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / 'session_tapes.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload))
        temporary.replace(path)

    def restore_session(self):
        try:
            payload = json.loads((self.cache_dir / 'session_tapes.json').read_text())
            if payload.get('date') != datetime.now(IST).date().isoformat():
                return
            with self.scanner.lock:
                for symbol, tape in payload.get('tapes', {}).items():
                    rec = self.scanner.records.get(symbol)
                    if rec and isinstance(tape, list):
                        # Merge by minute; current observations win. Recovery remains
                        # blocked until broker history has repaired any missing interval.
                        merged = {x['minute']: x for x in tape if isinstance(x, dict) and 'minute' in x}
                        merged.update({x['minute']: x for x in rec.get('minuteTape', [])})
                        rec['minuteTape'] = [merged[k] for k in sorted(merged)][-68:]
        except (OSError, ValueError, TypeError, KeyError):
            LOG.warning('Session checkpoint unavailable; broker recovery will rebuild it')

    def recover_signal_history(self, symbol):
        """Session bars for every symbol, priority leaders first; independent of UI."""
        generation = self.connection_generation
        now = datetime.now(IST)
        opening = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now < opening or symbol not in self.cash:
            return
        with self.scanner.lock:
            rec = self.scanner.records.get(symbol)
            needs_tape = bool(rec and not rec.get('historyRecovered'))
        if rec is None:
            return
        minute_bars = None
        if needs_tape:
            minute_bars = self.rate_limited_history(self.cash[symbol], opening, now, 'minute')
            if not minute_bars:
                return
        bars = self.rate_limited_history(self.cash[symbol], opening, now, '5minute')
        if not bars or generation != self.connection_generation:
            return
        with self.scanner.lock:
            if generation != self.connection_generation:
                return
            self.scanner.seed_candles(symbol, bars, chart=False)
            rec = self.scanner.records[symbol]
            if minute_bars is not None:
                from fresh_momentum import update_minute_tape
                tape = []
                cumulative = 0
                for bar in sorted(minute_bars, key=lambda b: as_ist(b['date'])):
                    at = as_ist(bar['date']) + timedelta(minutes=1)
                    if at > now or at.date() != now.date():
                        continue
                    cumulative += int(bar.get('volume', 0))
                    tape = update_minute_tape(tape, at, float(bar['close']), cumulative)
                expected_minutes = {int((opening + timedelta(minutes=i)).timestamp()//60)
                                    for i in range(1, int((now-opening).total_seconds()//60)+1)}
                observed_minutes = {int((as_ist(b['date']) + timedelta(minutes=1)).timestamp()//60)
                                    for b in minute_bars if as_ist(b['date']) + timedelta(minutes=1) <= now}
                if not expected_minutes or not expected_minutes.issubset(observed_minutes):
                    return  # A gap cannot be treated as a zero-volume minute.
                # Stream snapshots are more current within a sampled minute.
                merged = {x['minute']: x for x in tape}
                merged.update({x['minute']: x for x in rec.get('minuteTape', [])
                               if x.get('day') == now.date().isoformat()})
                rec['minuteTape'] = [merged[k] for k in sorted(merged)][-68:]
            # Missing opening bars must not silently authorize signals.
            rec['historyRecovered'] = bool(rec.get('volumeCutoff') and
                                           str(rec['volumeCutoff'])[:10] == now.date().isoformat())
            self.signal_hydrated[symbol] = (now.date().isoformat(), int(now.timestamp()//300))

    async def signal_recovery_loop(self):
        await asyncio.to_thread(self.restore_session)
        while not self.stop_requested:
            now = datetime.now(IST)
            if now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 35):
                with self.scanner.lock:
                    rows = [self.scanner.public_record(r) for r in self.scanner.records.values()]
                    priority = []
                    for bullish in (True, False):
                        side = sorted((r for r in rows if (r['change'] >= 0) == bullish),
                                      key=lambda r: r['adaptiveRankScore'], reverse=True)
                        priority.extend(r['symbol'] for r in side[:6])
                    priority.extend(s for names in self.scanner.top3_locked.values() for s in names)
                for symbol in dict.fromkeys(priority + list(self.cash)):
                    if self.stop_requested:
                        break
                    now = datetime.now(IST)
                    key = (now.date().isoformat(), int(now.timestamp()//300))
                    if self.signal_hydrated.get(symbol) == key:
                        continue
                    try:
                        await asyncio.to_thread(self.recover_signal_history, symbol)
                    except Exception as exc:
                        LOG.warning('Signal recovery failed for %s (%s)', symbol, type(exc).__name__)
                    await asyncio.sleep(.05)
                try:
                    await asyncio.to_thread(self.checkpoint_session)
                except OSError:
                    LOG.warning('Session checkpoint could not be saved')
            await asyncio.sleep(15)

    def close(self):
        self.stop_requested = True
        if self.recovery_task:
            self.recovery_task.cancel()
        if self.history_task:
            self.history_task.cancel()
        if self.ws is not None:
            try: self.ws.close()
            except Exception: pass
