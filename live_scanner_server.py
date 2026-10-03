"""Single-process Flask/Kite server for Pulse Premium."""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, time as clock, timedelta
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_file
from kiteconnect import KiteConnect, KiteTicker

from market_engine import (IST, OPEN, CLOSE, attach_boosters, attach_building, basket_rows, build_row,
                           complete_bars, market_context, market_open, normalize,
                           number, public_row, quote_fresh, refresh_row, sector_flow, timestamp)
from sector_definitions import ALL_SYMBOLS, SECTOR_DEFINITIONS

BASE_DIR = Path(__file__).resolve().parent
VERSION = "premium-2.2-building"
API_KEY = os.getenv("KITE_API_KEY", "").strip().strip('"\'')
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "").strip().strip('"\'')
DATA_DIR = Path(os.getenv("SCANNER_DATA_DIR", str(BASE_DIR / ".runtime")))
HISTORY_CACHE_PATH = DATA_DIR / "history-cache.json.gz"
SEED_DAYS_5M = int(os.getenv("SEED_DAYS_5M", "45"))
SEED_DAYS_DAILY = int(os.getenv("SEED_DAYS_DAILY", "120"))
HISTORY_SLEEP_SEC = float(os.getenv("HISTORY_SLEEP_SEC", ".4"))
COMPUTE_SEC = float(os.getenv("SCAN_COMPUTE_EVERY_SEC", "3"))
RECONCILE_SEC = float(os.getenv("RECONCILE_EVERY_SEC", "180"))
SETTINGS = {"quote_stale_sec": int(os.getenv("TICK_STALE_SEC", "20")),
            "cache_stale_sec": int(os.getenv("CACHE_STALE_SEC", "30")),
            "volume_sessions": int(os.getenv("VOLUME_BASELINE_SESSIONS", "20")),
            "min_volume_sessions": int(os.getenv("MIN_VOLUME_SESSIONS", "10")),
            "min_turnover": float(os.getenv("LIQUIDITY_MIN_TURNOVER", "50000000")),
            "max_spread_bps": float(os.getenv("LIQUIDITY_MAX_SPREAD_BPS", "10")),
            "min_side_depth": float(os.getenv("LIQUIDITY_MIN_SIDE_DEPTH", "100000")),
            "booster_rvol": float(os.getenv("BOOSTER_MIN_RVOL", "1.5")),
            "signal_ttl_sec": int(os.getenv("SIGNAL_TTL_SEC", "90")),
            "building_max_gap_pct": float(os.getenv("BUILDING_MAX_GAP_PCT", ".5")),
            "building_min_rvol": float(os.getenv("BUILDING_MIN_RVOL", "1.0"))}
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pulse")
app = Flask(__name__)
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 16384
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

DATA_LOCK = threading.RLock()
CACHE_LOCK = threading.RLock()
ACCESS_LOCK = threading.RLock()
INIT_LOCK = threading.Lock()
HISTORY = {}
TICKS = {}
LIVE_BARS = {}
SYMBOL_TO_TOKEN = {}
TOKEN_TO_SYMBOL = {}
INDEX_TOKEN = None
KITE = None
TICKER = None
TICKER_CONNECTED = False
LIVE_INITIALIZED = False
TOTAL_TICKS = 0
LAST_TICK_TS = 0.0
FEED_ERROR = None
SEED_PROGRESS = {"done": 0, "total": 0, "errors": 0}
SEED_IN_PROGRESS = False
HISTORY_SEED_DATE = None
LAST_SEED_ATTEMPT = None
ACTIVE_ACCESS_SESSIONS = {}
ACCESS_TTL_SEC = int(os.getenv("ACCESS_TTL_SEC", "43200"))
SIGNAL_IDS = set()
FEATURE_CACHE = {}
CACHE = {"intraday": [], "regular": [], "context": None, "sector_flow": [], "updated_at": None}
PRIMARY_SECTOR = {}
for key, symbols in SECTOR_DEFINITIONS.items():
    if key != "NIFTY_50":
        for symbol in symbols:
            PRIMARY_SECTOR.setdefault(symbol, key)
INDEX_GROUPS = {"NIFTY 50": SECTOR_DEFINITIONS["NIFTY_50"],
                "BANK NIFTY": SECTOR_DEFINITIONS["BANK"] + SECTOR_DEFINITIONS["PSUBANK"],
                "NIFTY IT": SECTOR_DEFINITIONS["IT"], "NIFTY AUTO": SECTOR_DEFINITIONS["AUTO"],
                "NIFTY METAL": SECTOR_DEFINITIONS["METAL"], "NIFTY PHARMA": SECTOR_DEFINITIONS["PHARMA"]}


def _now():
    return datetime.now(IST)


def normalize_access_number(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[2:] if len(digits) == 12 and digits.startswith("91") else digits


def allowed_access_numbers():
    configured = os.getenv("ACCESS_NUMBERS_FILE", "").strip()
    if configured:
        path = Path(configured)
        paths = [path if path.is_absolute() else BASE_DIR / path]
    else:
        paths = [Path("/etc/secrets/numbers.txt"), BASE_DIR / "numbers.txt"]
    for path in paths:
        try:
            values = path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            log.error("Access allowlist cannot be read at %s; access is disabled", path)
            return set()
        numbers = set()
        for line in values:
            value = normalize_access_number(line.partition("#")[0])
            if len(value) == 10 and value.isascii():
                numbers.add(value)
        if not numbers:
            log.error("Access allowlist has no valid entries at %s; access is disabled", path)
        return numbers
    log.error("Access allowlist missing; add numbers.txt as a Render Secret File or set ACCESS_NUMBERS_FILE")
    return set()


def access_session_is_active(phone, session_id):
    with ACCESS_LOCK:
        record = ACTIVE_ACCESS_SESSIONS.get(phone)
        if not record or not session_id or record["session_id"] != session_id or time.time() - record["ts"] > ACCESS_TTL_SEC:
            return False
        record["ts"] = time.time()
        return True


def _sdk_timestamp(value):
    # Kite's decoder uses datetime.fromtimestamp(), producing host-local naive times.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.astimezone(IST)
    return timestamp(value)


def _update_tick(tick, received=None):
    """Retain exchange timestamps and form full-session provisional five-minute bars."""
    received = received or _now()
    token, price = tick.get("instrument_token"), number(tick.get("last_price"))
    if token is None or price is None or price <= 0:
        return False
    source = _sdk_timestamp(tick.get("exchange_timestamp"))
    if source is None or not 0 <= (received - source).total_seconds() <= SETTINGS["quote_stale_sec"]:
        return False
    token = int(token)
    previous = TICKS.get(token, {})
    old_stamp = timestamp(previous.get("source_at"))
    if old_stamp is not None and source < old_stamp:
        return False
    volume = number(tick.get("volume_traded"))
    previous_volume = number(previous.get("volume"))
    same_session = old_stamp is not None and old_stamp.date() == source.date()
    if same_session and volume is not None and previous_volume is not None and volume < previous_volume:
        return False
    TICKS[token] = {"ltp": price, "volume": volume, "average_price": number(tick.get("average_traded_price")),
                    "ohlc": dict(tick.get("ohlc") or {}), "depth": dict(tick.get("depth") or {}),
                    "source_at": source.isoformat(), "received_at": received.isoformat(),
                    "trade_at": _sdk_timestamp(tick.get("last_trade_time")).isoformat() if _sdk_timestamp(tick.get("last_trade_time")) is not None else None}
    if token not in TOKEN_TO_SYMBOL or not OPEN <= source.time() < CLOSE or source.date() != received.date():
        return True
    trade = _sdk_timestamp(tick.get("last_trade_time"))
    # Book-only updates do not invent trades or repair an old LTP candle.
    if trade is None or trade.date() != source.date() or abs((source - trade).total_seconds()) > SETTINGS["quote_stale_sec"]:
        return True
    start = source.replace(hour=9, minute=15, second=0, microsecond=0)
    slot = int((trade - start).total_seconds() // 300)
    if slot < 0:
        return True
    bucket = start + timedelta(minutes=5 * slot)
    bars = LIVE_BARS.setdefault(token, {})
    if bars and next(iter(bars)).date() != source.date():
        bars.clear()
    contiguous = same_session and old_stamp is not None and 0 <= (source - old_stamp).total_seconds() <= SETTINGS["quote_stale_sec"]
    for at, preceding in bars.items():
        if at < bucket and not preceding["complete"]:
            preceding["complete"] = bool(at + timedelta(minutes=5) == bucket and contiguous and preceding.get("continuous"))
            preceding["continuous"] = False
    bar = bars.setdefault(bucket, {"date": bucket, "open": price, "high": price, "low": price,
                                   "close": price, "volume": 0.0, "complete": False, "continuous": contiguous})
    bar["high"], bar["low"], bar["close"] = max(bar["high"], price), min(bar["low"], price), price
    if not contiguous:
        bar["continuous"] = False
    if contiguous and volume is not None and previous_volume is not None:
        bar["volume"] += max(0, volume - previous_volume)
    return True


def _history_state(token, now, frozen=None):
    if frozen is None:
        with DATA_LOCK:
            frozen = {"history": dict(HISTORY.get(token, {})), "bars": [dict(bar) for bar in LIVE_BARS.get(token, {}).values()],
                      "tick": dict(TICKS.get(token, {}))}
    history = {key: frame.copy() for key, frame in frozen["history"].items()}
    provisional = [bar for bar in frozen["bars"] if bar["complete"] and bar["date"] + timedelta(minutes=5) <= now]
    tick = frozen["tick"]
    five = history.get("intraday", normalize([]))
    if provisional:
        # Completed broker history is authoritative over sampled-quote bars.
        live = normalize(provisional)
        authoritative = complete_bars(five, now)
        five = normalize(pd.concat([live, authoritative], ignore_index=True)) if not authoritative.empty else live
    history["intraday"] = complete_bars(five, now)
    history.setdefault("regular", normalize([]))
    return history, tick


def _load_history_cache():
    global HISTORY_SEED_DATE
    try:
        if not HISTORY_CACHE_PATH.exists() or HISTORY_CACHE_PATH.stat().st_size > 30000000:
            return False
        with gzip.open(HISTORY_CACHE_PATH, "rt", encoding="utf-8") as handle:
            raw = handle.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            return False
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("version") != 2 or not isinstance(payload.get("history"), dict):
            return False
        loaded = {}
        for symbol, histories in payload.get("history", {}).items():
            token = SYMBOL_TO_TOKEN.get(symbol)
            if token and isinstance(histories, dict):
                loaded[token] = {key: normalize(histories.get(key, [])) for key in ("intraday", "regular")}
        with DATA_LOCK:
            HISTORY.update(loaded)
        HISTORY_SEED_DATE = str(payload.get("seed_date"))
        return bool(loaded)
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        log.exception("Ignoring invalid JSON history cache")
        return False


def _save_history_cache():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with DATA_LOCK:
            history = {TOKEN_TO_SYMBOL[token]: {key: [{**record, "date": record["date"].isoformat()} for record in frame.to_dict("records")]
                       for key, frame in histories.items()} for token, histories in HISTORY.items() if token in TOKEN_TO_SYMBOL}
        payload = {"version": 2, "seed_date": HISTORY_SEED_DATE, "history": history}
        temporary = HISTORY_CACHE_PATH.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False, separators=(",", ":"))
        temporary.replace(HISTORY_CACHE_PATH)
    except (OSError, ValueError):
        log.exception("Could not persist market history")


def _seed_history():
    global SEED_IN_PROGRESS, HISTORY_SEED_DATE, LAST_SEED_ATTEMPT
    if KITE is None or SEED_IN_PROGRESS:
        return
    SEED_IN_PROGRESS = True
    now = _now()
    LAST_SEED_ATTEMPT = now
    SEED_PROGRESS.update(done=0, errors=0, total=len(SYMBOL_TO_TOKEN))
    try:
        for symbol, token in list(SYMBOL_TO_TOKEN.items()):
            try:
                five = KITE.historical_data(token, now - timedelta(days=SEED_DAYS_5M), now, "5minute")
                time.sleep(HISTORY_SLEEP_SEC)
                daily = KITE.historical_data(token, now - timedelta(days=SEED_DAYS_DAILY), now, "day")
                with DATA_LOCK:
                    HISTORY[token] = {"intraday": complete_bars(normalize(five), now),
                                      "regular": complete_bars(normalize(daily), now, daily=True)}
            except Exception:
                SEED_PROGRESS["errors"] += 1
                log.exception("History seed failed for %s", symbol)
            finally:
                SEED_PROGRESS["done"] += 1
                time.sleep(HISTORY_SLEEP_SEC)
        if not SEED_PROGRESS["errors"]:
            HISTORY_SEED_DATE = now.date().isoformat()
        _save_history_cache()
    finally:
        SEED_IN_PROGRESS = False


def _reconcile_history(allow_closed=False):
    """Paced broker backfill corrects sampled quote bars and reconnect gaps."""
    if KITE is None or SEED_IN_PROGRESS or (not market_open(_now()) and not allow_closed):
        return
    for symbol, token in list(SYMBOL_TO_TOKEN.items()):
        now = _now()
        if (not market_open(now) and not allow_closed) or SEED_IN_PROGRESS:
            break
        try:
            start = now.replace(hour=9, minute=15, second=0, microsecond=0)
            candles = complete_bars(normalize(KITE.historical_data(token, start, now, "5minute")), now)
            with DATA_LOCK:
                frames = HISTORY.setdefault(token, {"intraday": normalize([]), "regular": normalize([])})
                frames["intraday"] = normalize(pd.concat([frames["intraday"], candles], ignore_index=True))
        except Exception:
            log.warning("Candle reconciliation failed for %s", symbol, exc_info=True)
        time.sleep(HISTORY_SLEEP_SEC)


def _record_signals(rows, now):
    if not market_open(now):
        return
    if SIGNAL_IDS and not next(iter(SIGNAL_IDS)).startswith(str(now.date())):
        SIGNAL_IDS.clear()
    for row in rows:
        signal = row.get("booster") or {}
        if signal.get("status") != "eligible" or signal["id"] in SIGNAL_IDS:
            continue
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            record = {"version": VERSION, "published_at": now.isoformat(), "signal": signal,
                      "row": public_row(row), "settings": SETTINGS}
            with (DATA_DIR / f"signals-{now.date()}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, allow_nan=False) + "\n")
            SIGNAL_IDS.add(signal["id"])
        except (OSError, ValueError):
            log.exception("Signal logging failed")


def _refresh_scan_cache(now=None):
    with DATA_LOCK:
        instruments = list(SYMBOL_TO_TOKEN.items())
        snapshots = {token: {"history": dict(HISTORY.get(token, {})),
                            "bars": [dict(bar) for bar in LIVE_BARS.get(token, {}).values()],
                            "tick": dict(TICKS.get(token, {}))} for _, token in instruments}
        index_tick = dict(TICKS.get(INDEX_TOKEN, {}))
        now = now or _now()
    intraday, regular = [], []
    for symbol, token in instruments:
        frozen, history = snapshots[token], None
        tick = frozen["tick"]
        sector = PRIMARY_SECTOR.get(symbol, "OTHER")
        closed = [bar for bar in frozen["bars"] if bar["complete"]]
        marker = tuple((bar["date"], bar["close"], bar["high"], bar["low"], bar["volume"]) for bar in closed[-2:])
        feature_key = (now.date(), now.hour, now.minute // 5,
                       (tick.get("source_at") or "")[:10], bool(tick.get("ltp")),
                       tuple((key, id(frame)) for key, frame in frozen["history"].items()), marker, tuple(SETTINGS.values()))
        for mode, destination in (("intraday", intraday), ("regular", regular)):
            entry = FEATURE_CACHE.get((token, mode))
            if entry is None or entry["key"] != feature_key:
                if history is None:
                    history, _ = _history_state(token, now, frozen)
                row = build_row(symbol, sector, history, tick, now, SETTINGS, mode)
                FEATURE_CACHE[(token, mode)] = {"key": feature_key, "row": row}
            else:
                row = refresh_row(entry["row"], tick, now, SETTINGS, mode) if entry["row"] else None
            if row:
                destination.append(row)
    context = market_context(intraday, index_tick, set(SECTOR_DEFINITIONS["NIFTY_50"]), now, SETTINGS)
    attach_boosters(intraday, context, now, SETTINGS)
    attach_building(intraday, context, now, SETTINGS)
    published_at = _now()
    if (published_at - now).total_seconds() <= SETTINGS["quote_stale_sec"]:
        _record_signals(intraday, published_at)
    for rows in (intraday, regular):
        rows.sort(key=lambda row: row["score"] if row["score"] is not None else -1, reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
    with CACHE_LOCK:
        CACHE.update(intraday=[public_row(row) for row in intraday], regular=[public_row(row) for row in regular],
                     context=context, sector_flow=sector_flow(intraday), updated_at=published_at.isoformat())


def _start_ticker():
    global TICKER
    if KITE is None or TICKER is not None:
        return
    TICKER = KiteTicker(API_KEY, ACCESS_TOKEN, reconnect=True, reconnect_max_tries=50)

    def on_connect(ws, _response):
        global TICKER_CONNECTED, FEED_ERROR
        tokens = list(TOKEN_TO_SYMBOL) + ([INDEX_TOKEN] if INDEX_TOKEN else [])
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        TICKER_CONNECTED, FEED_ERROR = True, None

    def on_ticks(_ws, ticks):
        global TOTAL_TICKS, LAST_TICK_TS
        received = _now()
        with DATA_LOCK:
            accepted = sum(bool(_update_tick(tick, received)) for tick in ticks)
            TOTAL_TICKS += accepted
            if accepted:
                LAST_TICK_TS = time.time()

    def disconnected(_ws, _code, reason):
        global TICKER_CONNECTED, FEED_ERROR
        TICKER_CONNECTED, FEED_ERROR = False, "ticker_disconnected"
        log.warning("Kite feed disconnected: %s", reason)

    TICKER.on_connect, TICKER.on_ticks = on_connect, on_ticks
    TICKER.on_close, TICKER.on_error = disconnected, disconnected
    TICKER.connect(threaded=True)


def _compute_loop():
    while True:
        started = time.monotonic()
        try:
            _refresh_scan_cache()
        except Exception:
            log.exception("Scanner calculation failed; old cache will expire")
        time.sleep(max(.1, COMPUTE_SEC - (time.monotonic() - started)))


def _history_loop():
    last_reconcile = 0.0
    while True:
        now = _now()
        seed_due = now.weekday() < 5 and now.time() >= clock(7, 30) and HISTORY_SEED_DATE != now.date().isoformat()
        retry_due = LAST_SEED_ATTEMPT is None or (now - LAST_SEED_ATTEMPT).total_seconds() >= 600
        if seed_due and retry_due:
            _seed_history()
        after_close = now.weekday() < 5 and clock(15, 32) <= now.time() < clock(16, 0)
        if (market_open(now) or after_close) and time.monotonic() - last_reconcile >= RECONCILE_SEC:
            _reconcile_history(allow_closed=after_close)
            last_reconcile = time.monotonic()
            _save_history_cache()
        time.sleep(15)


def initialize_live():
    global LIVE_INITIALIZED
    with INIT_LOCK:
        if LIVE_INITIALIZED:
            return
        LIVE_INITIALIZED = True
    threading.Thread(target=_compute_loop, name="pulse-compute", daemon=True).start()

    def initialize():
        global KITE, INDEX_TOKEN, FEED_ERROR
        if not API_KEY or not ACCESS_TOKEN:
            FEED_ERROR = "missing_credentials"
            return
        try:
            KITE = KiteConnect(api_key=API_KEY)
            KITE.set_access_token(ACCESS_TOKEN)
            instruments = KITE.instruments("NSE")
            with DATA_LOCK:
                for instrument in instruments:
                    symbol, token = instrument["tradingsymbol"], int(instrument["instrument_token"])
                    if symbol in ALL_SYMBOLS and instrument.get("instrument_type") == "EQ":
                        SYMBOL_TO_TOKEN[symbol], TOKEN_TO_SYMBOL[token] = token, symbol
                    elif symbol in {"NIFTY 50", "NIFTY50"}:
                        INDEX_TOKEN = token
            _load_history_cache()
            _start_ticker()
            if not HISTORY:
                _seed_history()
            threading.Thread(target=_history_loop, name="pulse-history", daemon=True).start()
        except Exception:
            FEED_ERROR = "initialization_failed"
            log.exception("Market-data initialization failed")
    threading.Thread(target=initialize, name="pulse-startup", daemon=True).start()


def ensure_live_started():
    if not app.config.get("TESTING"):
        initialize_live()


def _feed_status(now):
    if not API_KEY or not ACCESS_TOKEN:
        return "missing_credentials", False
    if not market_open(now):
        return "previous_session", False
    with DATA_LOCK:
        fresh = any(quote_fresh(tick, now, SETTINGS["quote_stale_sec"]) for token, tick in TICKS.items() if token in TOKEN_TO_SYMBOL)
    with CACHE_LOCK:
        cache_at = timestamp(CACHE.get("updated_at"))
    cache_fresh = cache_at is not None and 0 <= (now - cache_at).total_seconds() <= SETTINGS["cache_stale_sec"]
    if SEED_IN_PROGRESS:
        return "seeding", False
    if TICKER_CONNECTED and fresh and cache_fresh:
        return "live", True
    return "waiting_for_ticks", False


@app.after_request
def secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
def index():
    ensure_live_started()
    return send_file(BASE_DIR / "intraday-momentum-scanner.html")


@app.post("/api/access/login")
def access_login():
    ensure_live_started()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="invalid_request"), 400
    phone = normalize_access_number(payload.get("phone"))
    session_id = str(payload.get("session_id", "")).strip()
    if len(phone) != 10 or not 16 <= len(session_id) <= 128:
        return jsonify(ok=False, error="invalid_request"), 400
    allowed = allowed_access_numbers()
    if not allowed:
        return jsonify(ok=False, error="access_not_configured"), 503
    if phone not in allowed:
        return jsonify(ok=False, error="not_allowed"), 403
    with ACCESS_LOCK:
        current = ACTIVE_ACCESS_SESSIONS.get(phone)
        if current and time.time() - current["ts"] <= ACCESS_TTL_SEC and current["session_id"] != session_id:
            return jsonify(ok=False, error="already_logged_in"), 409
        ACTIVE_ACCESS_SESSIONS[phone] = {"session_id": session_id, "ts": time.time()}
    return jsonify(ok=True)


@app.post("/api/access/logout")
def access_logout():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="invalid_request"), 400
    phone, session_id = normalize_access_number(payload.get("phone")), payload.get("session_id")
    with ACCESS_LOCK:
        if ACTIVE_ACCESS_SESSIONS.get(phone, {}).get("session_id") == session_id:
            ACTIVE_ACCESS_SESSIONS.pop(phone, None)
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    ensure_live_started()
    now = _now()
    status, live = _feed_status(now)
    with CACHE_LOCK:
        cache_at = CACHE["updated_at"]
    return jsonify(status=status, live=live, server_time=now.isoformat(), market_open=market_open(now),
                   seed=dict(SEED_PROGRESS), symbols=len(SYMBOL_TO_TOKEN), ticks=TOTAL_TICKS,
                   cache_updated_at=cache_at, history_seed_date=HISTORY_SEED_DATE,
                   feed_error=FEED_ERROR, version=VERSION)


@app.get("/api/scan")
def scan():
    ensure_live_started()
    phone = normalize_access_number(request.args.get("access_phone"))
    session_id = request.args.get("access_session_id", "")
    if not access_session_is_active(phone, session_id):
        return jsonify(error="access_required"), 403
    mode, universe = request.args.get("type", "intraday"), request.args.get("universe", "stocks")
    sector = request.args.get("sector", "ALL").upper()
    if mode not in {"intraday", "regular"} or universe not in {"stocks", "index"}:
        return jsonify(error="invalid_scan_mode"), 400
    if sector not in SECTOR_DEFINITIONS and sector != "ALL":
        return jsonify(error="unknown_sector"), 400
    now = _now()
    status, live = _feed_status(now)
    with CACHE_LOCK:
        rows = deepcopy(CACHE[mode])
        context, cache_at = dict(CACHE["context"] or {}), CACHE["updated_at"]
    context_at = timestamp(context.get("as_of"))
    context_fresh = bool(live and context.get("session_date") == now.date().isoformat() and
                         context_at is not None and context_at.date() == now.date() and
                         0 <= (now - context_at).total_seconds() <= SETTINGS["quote_stale_sec"])
    if not context_fresh:
        context.update(regime="stale", agreement=None)
    if sector != "ALL":
        members = set(SECTOR_DEFINITIONS[sector])
        rows = [row for row in rows if row["symbol"] in members]
    for row in rows:
        source = timestamp(row.get("quote_as_of"))
        row["fresh"] = bool(live and row["fresh"] and row.get("session_date") == now.date().isoformat() and
                            source is not None and source.date() == now.date() and
                            0 <= (now - source).total_seconds() <= SETTINGS["quote_stale_sec"])
        if not row["fresh"]:
            row["liquidity_eligible"] = False
            if row["booster"]:
                row["booster"].update(status="invalidated", reason="Quote or API cache is stale")
        elif row["booster"] and row["booster"].get("status") == "eligible":
            expiry = timestamp(row["booster"].get("expires_at"))
            if expiry is None or now >= expiry or not context_fresh:
                row["booster"].update(status="invalidated", reason="Signal or market context expired")
        if mode != "intraday":
            row["building"] = None
        elif row.get("building"):
            expiry = timestamp(row["building"].get("expires_at"))
            if (not row["fresh"] or not context_fresh or context.get("regime") not in {"bullish", "bearish", "neutral"} or
                    expiry is None or now >= expiry):
                row["building"].update(status="invalidated", reason="Stock, market context or building snapshot expired")
    flow = sector_flow(rows)
    if not live:
        context.update(regime="stale", agreement=None)
    if universe == "index":
        rows = basket_rows(rows, INDEX_GROUPS)
    return jsonify(live=live, status=status, market_open=market_open(now), server_time=now.isoformat(),
                   cache_updated_at=cache_at, updated_at=cache_at, seed=dict(SEED_PROGRESS),
                   fast_mode=False, universe_size=len(SYMBOL_TO_TOKEN), detail_symbols=len(SYMBOL_TO_TOKEN),
                   definitions=SECTOR_DEFINITIONS, context=context, sector_flow=flow,
                   config={key: SETTINGS[key] for key in ("quote_stale_sec", "cache_stale_sec")},
                    booster_settings={"experimental": True, "min_rvol": SETTINGS["booster_rvol"],
                                      "baseline_sessions": SETTINGS["volume_sessions"], "ttl_sec": SETTINGS["signal_ttl_sec"]},
                    building_settings={"experimental": True, "max_gap_pct": SETTINGS["building_max_gap_pct"],
                                       "min_rvol": SETTINGS["building_min_rvol"]},
                   ticks=TOTAL_TICKS, rows=rows)


def start():
    initialize_live()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8050")), threaded=True, debug=False)


if __name__ == "__main__":
    start()
