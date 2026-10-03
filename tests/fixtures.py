"""Synthetic fixtures and side-effect guards for standard-library unittest."""

import importlib
import json
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

import market_engine as engine

# Never resolve real credentials when importing the application for tests.
with patch("os.getenv", side_effect=lambda _key, default=None: default):
    server = importlib.import_module("live_scanner_server")
server.app.config["TESTING"] = True

NOW = datetime(2026, 9, 28, 10, 2, tzinfo=engine.IST)
PHONE = "0000000000"
SESSION = "test-session-alpha-0001"
SETTINGS = {
    "quote_stale_sec": 20, "cache_stale_sec": 30,
    "volume_sessions": 20, "min_volume_sessions": 10,
    "min_turnover": 50_000_000, "max_spread_bps": 10,
    "min_side_depth": 100_000, "booster_rvol": 1.5,
    "signal_ttl_sec": 90,
}


def candle(at, close=100.0, volume=100.0, opening=None, high=None, low=None):
    return {"date": at, "open": close if opening is None else opening,
            "high": close + 1 if high is None else high,
            "low": close - 1 if low is None else low,
            "close": close, "volume": volume}


def session(day, volumes, close=100.0):
    start = day.replace(hour=9, minute=15, second=0, microsecond=0)
    return [candle(start + timedelta(minutes=5 * i), close, volume)
            for i, volume in enumerate(volumes)]


def weekdays(count, before=NOW):
    days, day = [], before
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return sorted(days)


def history(prior_sessions=20, current_slots=9, now=NOW):
    five = [bar for day in weekdays(prior_sessions, now)
            for bar in session(day, [100] * 75)]
    five += session(now, [200] * current_slots)
    daily = [candle(day.replace(hour=0, minute=0), close=95, volume=7_500)
             for day in weekdays(30, now)]
    return {"intraday": engine.normalize(five), "regular": engine.normalize(daily)}


def quote(now=NOW, **overrides):
    tick = {"ltp": 101.0, "volume": 1_000_000, "average_price": 100.0,
            "ohlc": {"open": 100, "high": 102, "low": 99, "close": 95},
            "depth": {"buy": [{"price": 100.98, "quantity": 2_000}] * 5,
                      "sell": [{"price": 101.02, "quantity": 2_000}] * 5},
            "source_at": now.isoformat(), "received_at": now.isoformat()}
    tick.update(overrides)
    return tick


def broker_tick(now=NOW, **overrides):
    tick = {"instrument_token": 1, "last_price": 101, "volume_traded": 1_000_000,
            "average_traded_price": 100, "exchange_timestamp": now,
            "last_trade_time": now, "depth": quote(now)["depth"],
            "ohlc": quote(now)["ohlc"]}
    tick.update(overrides)
    return tick


def context_row(symbol, sector="BANK", now=NOW, **overrides):
    row = {"symbol": symbol, "sector": sector, "isIndex": False, "fresh": True,
           "change": 1.0, "direction": 1, "ratio": 2.0, "score": 80.0,
           "ltp": 101.0, "vwap": 100.0, "emaTrend5": .1,
           "_current_features": True, "quote_as_of": now.isoformat()}
    row.update(overrides)
    return row


def signal_row(side=1):
    now = NOW.replace(hour=9, minute=35, second=20)
    bars = session(now, [100] * 4, close=99.8)
    for bar in bars[:3]:
        bar.update(open=99.7, high=100.0, low=99.5, close=99.8)
    bars[-1].update(open=99.9, high=100.2, low=99.8, close=100.1)
    row = context_row("SYNTH", now=now, ltp=100.05, vwap=99.6)
    row.update(indicators_ready=True, liquidity_eligible=True,
               volume_baseline_sessions=20, recent=.2, emaTrend15=.1,
               _session=engine.normalize(bars))
    if side < 0:
        for bar in bars:
            old = dict(bar)
            bar.update(open=200 - old["open"], close=200 - old["close"],
                       high=200 - old["low"], low=200 - old["high"])
        row.update(direction=-1, change=-1.0, ltp=99.95, vwap=100.0,
                   recent=-.2, emaTrend5=-.1, emaTrend15=-.1,
                   _session=engine.normalize(bars))
    return now, row


def cached_row(symbol="SYNTH", now=NOW, **overrides):
    row = context_row(symbol, now=now)
    row.update(display=symbol, session_date=now.date().isoformat(), rank=1,
               liquidity_eligible=True, indicators_ready=True,
               booster={"status": "eligible", "id": "synthetic-signal",
                        "expires_at": (now + timedelta(seconds=60)).isoformat()})
    row.pop("_current_features")
    row.update(overrides)
    return row


class ServerCase(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        for name, value in {
            "API_KEY": "synthetic-key", "ACCESS_TOKEN": "synthetic-token",
            "HISTORY": {}, "TICKS": {}, "LIVE_BARS": {},
            "SYMBOL_TO_TOKEN": {"SYNTH": 1}, "TOKEN_TO_SYMBOL": {1: "SYNTH"},
            "INDEX_TOKEN": 99, "KITE": None, "TICKER": None,
            "TICKER_CONNECTED": True, "LIVE_INITIALIZED": False,
            "TOTAL_TICKS": 0, "LAST_TICK_TS": 0.0, "FEED_ERROR": None,
            "SEED_IN_PROGRESS": False, "HISTORY_SEED_DATE": None,
            "SEED_PROGRESS": {"done": 0, "total": 0, "errors": 0},
            "ACTIVE_ACCESS_SESSIONS": {}, "SIGNAL_IDS": set(), "FEATURE_CACHE": {},
            "SETTINGS": dict(SETTINGS),
            "CACHE": {"intraday": [], "regular": [], "context": {},
                      "sector_flow": [], "updated_at": NOW.isoformat()},
        }.items():
            stack.enter_context(patch.object(server, name, value))
        stack.enter_context(patch.dict(server.app.config, TESTING=True))
        self.clock = stack.enter_context(patch.object(server, "_now", return_value=NOW))
        stack.enter_context(patch.object(server.time, "time", return_value=10_000.0))
        self.allowlist = stack.enter_context(
            patch.object(server, "allowed_access_numbers", return_value={PHONE}))
        self.record_signals = stack.enter_context(patch.object(server, "_record_signals"))
        for name in ("_load_history_cache", "_save_history_cache"):
            stack.enter_context(patch.object(server, name))
        for name in ("initialize_live", "KiteConnect", "KiteTicker"):
            stack.enter_context(patch.object(server, name, side_effect=AssertionError(
                "Live startup/broker calls are forbidden in unit tests")))
        for name in ("socket.create_connection", "socket.socket.connect",
                     "threading.Thread.start", "pathlib.Path.write_text",
                     "pathlib.Path.write_bytes", "pathlib.Path.mkdir"):
            stack.enter_context(patch(name, side_effect=AssertionError(
                "Network, background threads and disk writes are forbidden")))
        self.client = server.app.test_client()

    def login(self, session_id=SESSION, phone=PHONE):
        return self.client.post("/api/access/login", json={
            "phone": phone, "session_id": session_id})

    def scan(self, **query):
        return self.client.get("/api/scan", query_string={
            "access_phone": PHONE, "access_session_id": SESSION, **query})

    def seed_cache(self, rows=None):
        rows = rows if rows is not None else [cached_row()]
        server.TICKS[1] = quote()
        server.CACHE.update(intraday=rows, regular=rows,
                            context={"regime": "bullish", "agreement": 100,
                                     "as_of": NOW.isoformat(),
                                     "session_date": NOW.date().isoformat()})

    def strict_json(self, response):
        def reject(value):
            self.fail("Non-finite JSON constant: " + value)
        return json.loads(response.get_data(as_text=True), parse_constant=reject)
