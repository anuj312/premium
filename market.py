"""Read-only Kite quote aggregation for a visual intraday momentum dashboard."""
from __future__ import annotations

import math
import random
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sectors import ALL_SYMBOLS, NIFTY_50_SET, SECTOR_ONLY, SYMBOL_SECTOR

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def pct_change(value: float, base: float) -> float | None:
    return round((value / base - 1.0) * 100, 2) if base > 0 else None


def build_up(price_delta: float | None, oi_delta: float | None) -> str:
    """Classify change SINCE THIS PROCESS CONNECTED, not day-over-day change."""
    if price_delta is None or oi_delta is None:
        return "WAITING FOR OI"
    if abs(price_delta) < 0.02 or abs(oi_delta) < 0.02:
        return "NEUTRAL"
    if price_delta > 0 and oi_delta > 0:
        return "LONG BUILD-UP"
    if price_delta < 0 and oi_delta > 0:
        return "SHORT BUILD-UP"
    if price_delta > 0 and oi_delta < 0:
        return "SHORT COVERING"
    return "LONG UNWINDING"


class LiveKiteProvider:
    """Kite Connect REST snapshots. Quotes are batched; API tokens stay server-side."""

    def __init__(self, api_key: str, access_token: str):
        from kiteconnect import KiteConnect

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.nse_symbols = set()
        self.nse_instruments: dict[str, dict[str, Any]] = {}
        self.futures: dict[str, dict[str, Any]] = {}
        self.missing_nse: list[str] = []
        self.missing_futures: list[str] = []
        self._load_instruments()

    def _load_instruments(self):
        nse_rows = self.kite.instruments("NSE")
        self.nse_instruments = {
            row["tradingsymbol"]: row for row in nse_rows
            if row.get("tradingsymbol") in ALL_SYMBOLS
            and row.get("instrument_type") == "EQ"
        }
        self.nse_symbols = set(self.nse_instruments)
        self.missing_nse = sorted(set(ALL_SYMBOLS) - self.nse_symbols)

        fut_rows = self.kite.instruments("NFO")
        today = now_ist().date()
        for row in fut_rows:
            if row.get("instrument_type") != "FUT":
                continue
            symbol = str(row.get("name", "")).upper()
            if symbol not in self.nse_symbols:
                continue
            expiry = row.get("expiry")
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry[:10])
            if not expiry or expiry < today:
                continue
            selected = self.futures.get(symbol)
            if selected is None or expiry < selected["expiry"]:
                self.futures[symbol] = {**row, "expiry": expiry}
        self.missing_futures = sorted(self.nse_symbols - self.futures.keys())

    def quotes(self) -> dict[str, dict]:
        keys = [f"NSE:{s}" for s in sorted(self.nse_symbols)]
        keys += [f"NFO:{x['tradingsymbol']}" for x in self.futures.values()]
        merged: dict[str, dict] = {}
        for start in range(0, len(keys), 480):
            if start:
                time.sleep(1.1)  # Avoid consecutive quote bursts if universe grows.
            merged.update(self.kite.quote(*keys[start : start + 480]))
        return merged


import logging
log = logging.getLogger("kite-neon.ticker")


class KiteTickerProvider(LiveKiteProvider):
    """One Kite WebSocket for NSE equities + nearest-expiry NFO futures.

    REST provides a single initial snapshot; thereafter /api/state and browser
    clients read the in-memory tick cache. There is no recurring REST polling.
    """

    feed_name = "ticker"

    def __init__(self, api_key: str, access_token: str):
        super().__init__(api_key, access_token)
        self.api_key = api_key
        self.access_token = access_token
        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}
        self._token_to_key: dict[int, str] = {}
        for symbol, row in self.nse_instruments.items():
            token = int(row.get("instrument_token") or 0)
            if token:
                self._token_to_key[token] = f"NSE:{symbol}"
        for row in self.futures.values():
            token = int(row.get("instrument_token") or 0)
            if token:
                self._token_to_key[token] = f"NFO:{row['tradingsymbol']}"
        self.connected = False
        self.tick_count = 0
        self.last_tick_at: datetime | None = None
        self.last_tick_monotonic: float | None = None
        self.last_error: str | None = None
        self.ticker = None

    @staticmethod
    def normalize_tick(tick: dict, previous: dict | None = None) -> dict:
        """Convert Python KiteTicker full-mode keys to kite.quote-like keys."""
        out = dict(previous or {})
        for key in ("last_price", "oi", "ohlc", "depth"):
            if key in tick:
                out[key] = tick[key]
        aliases = {
            "volume_traded": "volume",
            "average_traded_price": "average_price",
            "total_buy_quantity": "buy_quantity",
            "total_sell_quantity": "sell_quantity",
            "last_traded_quantity": "last_quantity",
        }
        for src, dest in aliases.items():
            if src in tick:
                out[dest] = tick[src]
        return out

    def start(self):
        """Call once in app lifespan, not during every snapshot render."""
        # Seed all symbols so inactive stocks still have last traded prices.
        seeded = super().quotes()
        with self._lock:
            self._cache.update(seeded)
        if not self._token_to_key:
            raise RuntimeError("No valid instrument_tokens found. Check NSE/NFO instrument dump.")
        from kiteconnect import KiteTicker
        kws = KiteTicker(self.api_key, self.access_token, reconnect=True)
        self.ticker = kws
        kws.on_connect = self.on_connect
        kws.on_ticks = self.on_ticks
        kws.on_close = self.on_close
        kws.on_error = self.on_error
        kws.on_reconnect = self.on_reconnect
        kws.on_noreconnect = self.on_noreconnect
        kws.connect(threaded=True)

    def stop(self):
        if self.ticker is not None:
            self.ticker.close()

    def on_connect(self, ws, response):
        tokens = list(self._token_to_key)
        with self._lock:
            self.connected = True
            self.last_error = None
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        log.info("KiteTicker connected; subscribed to %d cash/futures tokens", len(tokens))

    def on_ticks(self, ws, ticks):
        now = now_ist()
        with self._lock:
            for tick in ticks:
                key = self._token_to_key.get(tick.get("instrument_token"))
                if key:
                    item = self.normalize_tick(tick, self._cache.get(key))
                    # Per-symbol actual upstream tick reception time, not dashboard refresh time.
                    # Use a timezone-aware server receipt time. Some Kite timestamps
                    # are naive datetimes, which browsers can otherwise interpret in
                    # the viewer's local timezone and put ticks in the wrong bar.
                    item['tick_at'] = now.isoformat()
                    self._cache[key] = item
                    self.tick_count += 1
            if ticks:
                self.last_tick_at = now
                self.last_tick_monotonic = time.monotonic()

    def on_close(self, ws, code, reason):
        with self._lock:
            self.connected = False
            self.last_error = f"Disconnected: {code} / {reason}"
        log.warning("KiteTicker closed: %s %s; reconnect is enabled", code, reason)
        # Do not call ws.stop() here; that disables automatic reconnection.

    def on_error(self, ws, code, reason):
        with self._lock:
            self.last_error = f"WebSocket error: {code} / {reason}"
        log.error("KiteTicker error: %s %s", code, reason)

    def on_reconnect(self, ws, attempts_count):
        log.warning("KiteTicker reconnect attempt %s", attempts_count)

    def on_noreconnect(self, ws):
        with self._lock:
            self.connected = False
            self.last_error = "KiteTicker reconnection attempts exhausted"
        log.error("KiteTicker reconnection attempts exhausted")

    def quotes(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._cache)   # Only cached ticks, NOT REST API calls.

    def feed_info(self) -> dict:
        with self._lock:
            last = self.last_tick_at.isoformat() if self.last_tick_at else None
            elapsed = (time.monotonic() - self.last_tick_monotonic) if self.last_tick_monotonic else None
            market_now = now_ist()
            regular_hours = (market_now.weekday() < 5
                             and (9, 15) <= (market_now.hour, market_now.minute) < (15, 30))
            if self.last_error and not self.connected:
                state = "disconnected"
            elif not self.connected:
                state = "connecting"
            elif elapsed is None:
                state = "waiting_for_ticks"
            elif elapsed > 30:
                state = "stale" if regular_hours else "market_closed_or_idle"
            else:
                state = "streaming"
            return {
                "feed": "ticker", "feed_state": state,
                "ticker_connected": self.connected,
                "received_ticks": self.tick_count,
                "last_tick_at": last,
                "subscribed_tokens": len(self._token_to_key),
                "feed_error": self.last_error,
                "data_note": "REST initial snapshot then KiteTicker streaming; no tick during inactivity",
            }


class DemoProvider:
    """Synthetic quotes, never used as fallback for failing LIVE credentials."""

    def __init__(self):
        self.rng = random.Random(4283)
        self.nse_symbols = set(ALL_SYMBOLS)
        self.futures = {
            s: {"tradingsymbol": f"{s}DEMOFUT", "expiry": now_ist().date()}
            for s in ALL_SYMBOLS
        }
        self.missing_nse = []
        self.missing_futures = []
        self.state = {}
        sector_drifts = {sector: self.rng.uniform(-1.5, 1.6) for sector in SECTOR_ONLY}
        sector_drifts.update({"DURABLES": 1.9, "ENERGY": 0.8, "LOGISTICS": -1.2})
        for s in ALL_SYMBOLS:
            base = round(self.rng.uniform(85, 2350), 2)
            sector = SYMBOL_SECTOR.get(s, "OTHER")
            start_move = sector_drifts.get(sector, 0) + self.rng.gauss(0, 1.25)
            self.state[s] = {
                "close": base,
                "price": base * (1 + start_move / 100),
                "oi": self.rng.randint(60000, 3800000),
                "volume": self.rng.randint(20000, 1200000),
                "open": base * (1 + start_move / 160),
                "high": base,
                "low": base,
                "av": base,
            }

    def quotes(self) -> dict[str, dict]:
        result = {}
        for symbol, item in self.state.items():
            sector_bump = 0.0015 if SYMBOL_SECTOR.get(symbol) == "DURABLES" else 0
            drift = self.rng.gauss(0, .0009) + sector_bump
            item["price"] = max(1, item["price"] * (1 + drift))
            item["oi"] = max(100, int(item["oi"] * (1 + self.rng.gauss(.0002, .0014))))
            item["volume"] += self.rng.randint(250, 7000)
            item["high"] = max(item["high"], item["price"], item["open"])
            item["low"] = min(item["low"], item["price"], item["open"])
            item["av"] = .97 * item["av"] + .03 * item["price"]
            result[f"NSE:{symbol}"] = {
                "last_price": round(item["price"], 2),
                "ohlc": {"open": item["open"], "high": item["high"],
                         "low": item["low"], "close": item["close"]},
                "average_price": round(item["av"], 2),
                "volume": item["volume"],
                "buy_quantity": int(item["volume"] * self.rng.uniform(.25, .72)),
                "sell_quantity": int(item["volume"] * self.rng.uniform(.25, .72)),
            }
            result[f"NFO:{symbol}DEMOFUT"] = {"oi": item["oi"]}
        return result


class MarketEngine:
    def __init__(self, provider, mode: str):
        self.provider = provider
        self.mode = mode
        self.lock = threading.RLock()
        self.baselines: dict[str, tuple[float, int]] = {}
        self.histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=36))
        self.last_day = None
        self._snapshot: dict[str, Any] = {
            "mode": mode, "status": "starting", "stocks": [], "sectors": [],
            "timestamp": None, "error": None, "meta": {}, "summary": {},
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self._snapshot)

    def update(self) -> dict[str, Any]:
        ts = now_ist()
        with self.lock:
            if self.last_day != ts.date():
                self.baselines.clear()
                self.histories.clear()
                self.last_day = ts.date()
        quotes = self.provider.quotes()
        stocks: list[dict] = []
        for symbol in ALL_SYMBOLS:
            equity = quotes.get(f"NSE:{symbol}")
            if not equity:
                continue
            price = float(equity.get("last_price") or 0)
            if price <= 0:
                continue
            ohlc = equity.get("ohlc") or {}
            close = float(ohlc.get("close") or 0)
            fut = self.provider.futures.get(symbol)
            future_data = quotes.get(f"NFO:{fut['tradingsymbol']}", {}) if fut else {}
            oi = int(future_data.get("oi") or 0)
            with self.lock:
                if oi and symbol not in self.baselines:
                    self.baselines[symbol] = (price, oi)
                baseline = self.baselines.get(symbol)
                history = self.histories[symbol]
                history.append(round(price, 2))
                spark = list(history)
            price_since_connect = pct_change(price, baseline[0]) if baseline else None
            oi_since_connect = pct_change(oi, baseline[1]) if baseline and oi else None
            vol = int(equity.get("volume") or 0)
            buy = int(equity.get("buy_quantity") or 0)
            sell = int(equity.get("sell_quantity") or 0)
            stocks.append({
                "symbol": symbol,
                "sector": SYMBOL_SECTOR.get(symbol, "OTHER"),
                "nifty50": symbol in NIFTY_50_SET,
                "price": round(price, 2),
                "change_pct": pct_change(price, close),
                "change_abs": round(price - close, 2) if close else None,
                "prev_close": round(close, 2) if close else None,
                "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                "vwap_proxy": float(equity.get("average_price") or 0) or None,
                "volume": vol,
                "buy_qty": buy, "sell_qty": sell,
                "bid_ask_ratio": round(buy / sell, 2) if sell else None,
                "oi": oi or None,
                "oi_change_pct": oi_since_connect,
                "price_connect_pct": price_since_connect,
                "buildup": build_up(price_since_connect, oi_since_connect),
                "futures_contract": fut["tradingsymbol"] if fut else None,
                "futures_expiry": fut["expiry"].isoformat() if fut else None,
                "spark": spark,
                "tick_at": equity.get("tick_at"),
            })
        sectors = []
        for sector, symbols in SECTOR_ONLY.items():
            selected = [s for s in stocks if s["symbol"] in symbols and s["change_pct"] is not None]
            if not selected:
                continue
            avg = sum(s["change_pct"] for s in selected) / len(selected)
            sectors.append({
                "name": sector, "change_pct": round(avg, 2), "count": len(selected),
                "advances": sum(s["change_pct"] > 0 for s in selected),
                "declines": sum(s["change_pct"] < 0 for s in selected),
            })
        sectors.sort(key=lambda s: s["change_pct"], reverse=True)
        changeable = [s for s in stocks if s["change_pct"] is not None]
        n50 = [s for s in changeable if s["nifty50"]]
        summary = {
            "total": len(stocks),
            "advances": sum(s["change_pct"] > 0 for s in changeable),
            "declines": sum(s["change_pct"] < 0 for s in changeable),
            "flat": sum(s["change_pct"] == 0 for s in changeable),
            "average_pct": round(sum(s["change_pct"] for s in changeable) / len(changeable), 2) if changeable else None,
            "nifty50_basket_pct": round(sum(s["change_pct"] for s in n50) / len(n50), 2) if n50 else None,
        }
        output = {
            "mode": self.mode, "status": "ok", "timestamp": ts.isoformat(),
            "error": None, "stocks": stocks, "sectors": sectors,
            "summary": summary,
            "meta": {
                "configured_symbols": len(ALL_SYMBOLS),
                "missing_nse": self.provider.missing_nse,
                "missing_futures": self.provider.missing_futures,
                "oi_basis": "% change since application connected (not previous-day OI)",
                "price_basis": "% change vs previous NSE closing price",
                "sector_basis": "equal-weight mean of constituent daily percentage changes",
                "vwap_basis": "Kite average traded price (session volume-weighted average)",
                **(self.provider.feed_info() if hasattr(self.provider, "feed_info") else {"feed": "rest" if self.mode == "live" else "demo"}),
            },
        }
        with self.lock:
            self._snapshot = output
        return output

    def fail(self, message: str):
        with self.lock:
            self._snapshot = {**self._snapshot, "status": "error", "error": message}
