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
from momentum20 import momentum_multiple
from proscore import evaluate

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
        self.tick_listeners = []
        self.disconnect_listeners = []
        self.connection_generation = 0

    def add_tick_listener(self, callback):
        """Callback(key, raw_tick, aware_receipt_time) on the ticker thread."""
        with self._lock:
            self.tick_listeners.append(callback)

    def add_disconnect_listener(self, callback):
        with self._lock:
            self.disconnect_listeners.append(callback)

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
            self.connection_generation += 1
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        log.info("KiteTicker connected; subscribed to %d cash/futures tokens", len(tokens))

    def on_ticks(self, ws, ticks):
        now = now_ist()
        notifications = []
        with self._lock:
            listeners = tuple(self.tick_listeners)
            for tick in ticks:
                key = self._token_to_key.get(tick.get("instrument_token"))
                if key:
                    notifications.append((key, tick))
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
        # Avoid locking the quote cache while executing callbacks.
        for key, tick in notifications:
            for listener in listeners:
                try:
                    listener(key, tick, now)
                except Exception:
                    log.exception("Candle tick listener failed for %s", key)

    def on_close(self, ws, code, reason):
        with self._lock:
            self.connected = False
            self.last_error = f"Disconnected: {code} / {reason}"
        log.warning("KiteTicker closed: %s %s; reconnect is enabled", code, reason)
        for listener in tuple(self.disconnect_listeners):
            try:
                listener()
            except Exception:
                log.exception("Candle disconnection listener failed")
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
                "connection_generation": self.connection_generation,
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
            result[f"NFO:{symbol}DEMOFUT"] = {"oi": item["oi"],"last_price":round(item['price']*1.004,2)}
        return result


class MarketEngine:
    def __init__(self, provider, mode: str):
        self.provider = provider
        self.mode = mode
        self.lock = threading.RLock()
        self.baselines: dict[str, tuple[float, int]] = {}
        self.future_baselines: dict[str, tuple[float, int]] = {}
        self.pro_history: dict[str, dict] = {}
        self.pro_scores: dict[str, dict] = {}
        self.stream_5m = None  # Ticker-only price/volume cache, attached after provider setup.
        self.pro_progress = {"status":"pending", "processed":0,"total":len(ALL_SYMBOLS),"available":0}
        self.histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=36))
        self.last_day = None
        self.momentum20: dict[str, float] = {}
        self.momentum20_progress: dict[str, Any] = {"status":"pending", "processed":0, "total":len(ALL_SYMBOLS), "available":0}
        self._snapshot: dict[str, Any] = {
            "mode": mode, "status": "starting", "stocks": [], "sectors": [],
            "timestamp": None, "error": None, "meta": {}, "summary": {},
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self._snapshot)

    def set_momentum20_progress(self, *, status: str, processed: int, available: int, total: int):
        with self.lock:
            self.momentum20_progress = {"status":status, "processed":processed, "available":available,"total":total}

    def set_momentum20_baseline(self, symbol: str, average_daily_move_pct: float):
        with self.lock:
            if average_daily_move_pct > 0:
                self.momentum20[symbol] = average_daily_move_pct
                self._score_locked(symbol)

    def set_pro_progress(self, **values):
        with self.lock:
            self.pro_progress.update(values)

    def set_pro_history(self, symbol, context):
        """Replace completed 5m features atomically; only this method re-scores."""
        with self.lock:
            self.pro_history[symbol] = context
            self._score_locked(symbol)

    def _score_locked(self, symbol):
        context = self.pro_history.get(symbol)
        if not context:
            return
        snapshot = next((s for s in self._snapshot.get('stocks',[]) if s['symbol']==symbol), None)
        if not snapshot:
            return
        sector = next((s for s in self._snapshot.get('sectors',[]) if s['name']==snapshot['sector']), None)
        self.pro_scores[symbol] = evaluate(
            context['today'], context['warmup'],context['reference'],
            self.momentum20.get(symbol), snapshot.get('prev_close'),
            snapshot.get('futures_price_connect_pct'), snapshot.get('oi_change_pct'),
            sector.get('change_pct') if sector else None)

    def reset_momentum20(self):
        with self.lock:
            self.momentum20.clear()
            self.momentum20_progress = {"status":"loading", "processed":0, "total":len(ALL_SYMBOLS), "available":0}

    def update(self) -> dict[str, Any]:
        ts = now_ist()
        with self.lock:
            if self.last_day != ts.date():
                self.baselines.clear()
                self.future_baselines.clear()
                self.pro_history.clear()
                self.pro_scores.clear()
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
            future_price = float(future_data.get("last_price") or 0)
            with self.lock:
                if oi and symbol not in self.baselines:
                    self.baselines[symbol] = (price, oi)
                if oi and future_price and symbol not in self.future_baselines:
                    self.future_baselines[symbol] = (future_price, oi)
                baseline = self.baselines.get(symbol)
                future_baseline = self.future_baselines.get(symbol)
                pro_data = self.pro_scores.get(symbol)
                normal20 = self.momentum20.get(symbol)
                history = self.histories[symbol]
                history.append(round(price, 2))
                spark = list(history)
            price_since_connect = pct_change(price, baseline[0]) if baseline else None
            futures_price_since_connect = pct_change(future_price, future_baseline[0]) if future_baseline and future_price else None
            oi_since_connect = pct_change(oi, future_baseline[1]) if future_baseline and oi else None
            day_change = pct_change(price, close)
            score20 = momentum_multiple(day_change, normal20)
            vol = int(equity.get("volume") or 0)
            buy = int(equity.get("buy_quantity") or 0)
            sell = int(equity.get("sell_quantity") or 0)
            stocks.append({
                "symbol": symbol,
                "sector": SYMBOL_SECTOR.get(symbol, "OTHER"),
                "nifty50": symbol in NIFTY_50_SET,
                "price": round(price, 2),
                "change_pct": day_change,
                "avg_day_move_20d_pct": round(normal20,3) if normal20 is not None else None,
                "momentum_20d_x": score20,
                "momentum_score": score20,  # V11 signed x-multiple retained for compatibility
                "pro_score": pro_data['score'] if pro_data else None,
                "pro_direction": pro_data['direction'] if pro_data else None,
                "pro_components": pro_data['components'] if pro_data else {},
                "pro_asof": pro_data['asof'] if pro_data else None,
                "pro_reason": pro_data['reason'] if pro_data else '5M SCORING QUEUED',
                "pro_rvol": pro_data['rvol'] if pro_data else None,
                "pro_efficiency": pro_data['efficiency_pct'] if pro_data else None,
                "pro_persistence": pro_data['persistence'] if pro_data else None,
                "pro_flags": pro_data['flags'] if pro_data else [],
                "pro_coverage": pro_data['coverage'] if pro_data else None,
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
                "futures_price_connect_pct": futures_price_since_connect,
                "buildup": build_up(futures_price_since_connect, oi_since_connect),
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
            ranked20 = [s["momentum_20d_x"] for s in selected if s["momentum_20d_x"] is not None]
            sectors.append({
                "name": sector, "change_pct": round(avg, 2), "count": len(selected),
                "momentum_20d_x": round(sum(ranked20)/len(ranked20),2) if len(ranked20)==len(selected) else None,
                "momentum_20d_coverage": len(ranked20),
                "advances": sum(s["change_pct"] > 0 for s in selected),
                "declines": sum(s["change_pct"] < 0 for s in selected),
            })
        # Preserve original sector sorting until 20D baselines have finished loading.
        with self.lock:
            momentum_state = dict(self.momentum20_progress)
        if momentum_state['status']=='ready' and momentum_state['available']:
            sectors.sort(key=lambda s: (s['momentum_20d_x'] is not None, s['momentum_20d_x'] if s['momentum_20d_x'] is not None else float('-inf')), reverse=True)
        else:
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
                "sector_basis": "equal-weight daily % change; sector order uses average signed 20D momentum once baseline is ready",
                "momentum20_basis": "signed daily % change / mean absolute close-to-close daily % change over prior 20 completed trading sessions",
                "momentum_score_basis": "signed normalized multiple (x) over 20 prior completed sessions; bullish high-to-low, bearish low-to-high",
                "momentum20": momentum_state,
                "proscore": dict(self.pro_progress),
                "pro_score_basis": "0–100 score from closed NSE 5m candles. KiteTicker aggregates live candles after one-time history seed; REST history only for gaps/reconnect and on-demand other chart intervals.",
                "stream_5m": self.stream_5m.info() if self.stream_5m is not None else None,
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
