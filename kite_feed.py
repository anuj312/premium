"""Zerodha Kite Connect adapter for OrderFlow Studio.

This adapter is DATA-ONLY: it does not place, modify, or cancel orders.
API credentials live in server-side environment variables, never browser code.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sectors import ALL_SYMBOLS

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
    avp = quote.get('average_traded_price', quote.get('average_price'))
    return {'symbol': symbol, 'price': float(last), 'prev_close': float(prev),
            'open': ohlc.get('open'), 'volume': int(volume) if volume is not None else None,
            'average_price': float(avp) if avp else None, 'timestamp': as_ist(at).isoformat()}


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
            tokens = list(self.reverse_cash) + list(self.reverse_futures)
            ws.subscribe(tokens)
            if self.reverse_cash:
                ws.set_mode(ws.MODE_QUOTE, list(self.reverse_cash))
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
        except Exception as exc:
            # Don't return raw SDK exception contents to the browser (could contain secrets).
            LOG.error('Kite startup failed (%s)', type(exc).__name__)
            self.status('error', f'Kite initialization failed ({type(exc).__name__}); check credentials, API plan and server logs')

    def hydrate_history(self, symbol):
        """Lazy historical 5m backfill, cached ~60s and rate-limited to <=3/s."""
        if symbol not in self.cash:
            return
        lock = self.history_locks.setdefault(symbol, threading.Lock())
        with lock:
            now_monotonic = time.monotonic()
            if now_monotonic - self.history_timestamps.get(symbol, -1e12) < 60:
                return
            # Mark before call to avoid retry storms when the provider rejects history.
            self.history_timestamps[symbol] = now_monotonic
            now = datetime.now(IST)
            start = (now - timedelta(days=18)).replace(hour=9, minute=15, second=0, microsecond=0)
            # Around 12 trading sessions allow useful 60-minute candle history and order blocks.
            with self.history_rate:
                wait = max(0., self.last_history_call + .38 - time.monotonic())
                if wait: time.sleep(wait)
                self.last_history_call = time.monotonic()
            try:
                candles = self.kite.historical_data(self.cash[symbol], start, now, '5minute', oi=False)
                self.scanner.seed_candles(symbol, candles)
            except Exception as exc:
                LOG.warning('Historical data unavailable for %s (%s)', symbol, type(exc).__name__)
                # Quotes continue working; no made-up historical candles.

    def close(self):
        self.stop_requested = True
        if self.ws is not None:
            try: self.ws.close()
            except Exception: pass
