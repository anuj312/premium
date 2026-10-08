"""Launch via `uvicorn main:app --host 127.0.0.1 --port 8000`."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from market import DemoProvider, LiveKiteProvider, KiteTickerProvider, MarketEngine, now_ist
from charting import demo_candles, normalize_candles, last_completed_count, identify_order_blocks, CHART_INTERVALS
from sectors import ALL_SYMBOLS

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
MODE = os.getenv("MODE", "demo").strip().lower()
FEED = os.getenv("FEED", "ticks").strip().lower()
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "2")))
log = logging.getLogger("kite-neon")


def build_engine() -> MarketEngine:
    if MODE == "demo":
        return MarketEngine(DemoProvider(), "demo")
    if MODE != "live":
        raise RuntimeError("MODE must be 'demo' or 'live'")
    key = os.getenv("KITE_API_KEY", "").strip()
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not key or not token:
        raise RuntimeError("LIVE MODE requires KITE_API_KEY and KITE_ACCESS_TOKEN in .env")
    if FEED == "ticks":
        return MarketEngine(KiteTickerProvider(key, token), "live")
    if FEED == "rest":
        return MarketEngine(LiveKiteProvider(key, token), "live")
    raise RuntimeError("FEED must be 'ticks' or 'rest'")


def boot_snapshot(app: FastAPI) -> dict:
    """Return an informative state while Kite is loading in the background."""
    boot = app.state.boot
    return {
        "mode": MODE,
        "status": boot["status"],
        "stocks": [], "sectors": [], "summary": {},
        "timestamp": now_ist().isoformat(),
        "error": boot.get("error"),
        "meta": {
            "feed": "ticker" if FEED == "ticks" else "rest",
            "feed_state": "error" if boot["status"] == "error" else "seeding",
            "seed_stage": boot["stage"],
            "seed_step": boot["step"],
            "seed_total_steps": 3,
            "seed_started_at": boot["started_at"],
            "configured_symbols": len(ALL_SYMBOLS),
        },
    }


def current_state(app: FastAPI) -> dict:
    engine = app.state.engine
    return engine.snapshot() if engine is not None else boot_snapshot(app)


async def seed_market(app: FastAPI):
    """Run all slow network work in a thread *after* ASGI startup completes.

    The homepage and /healthz remain responsive during instrument download,
    initial REST quotes, and ticker setup. Never substitute demo prices in LIVE.
    """
    engine = None
    try:
        await asyncio.sleep(0)  # let FastAPI/uvicorn complete startup first
        app.state.boot.update(stage="LOADING NSE & NFO INSTRUMENTS", step=1)
        log.info("[SEED 1/3] Loading Kite NSE and nearest-expiry futures instruments")
        engine = await asyncio.to_thread(build_engine)
        provider = engine.provider
        app.state.boot.update(stage="SEEDING INITIAL MARKET QUOTES", step=2)
        log.info("[SEED 2/3] Fetching initial quotes; website is already serving")
        if hasattr(provider, "start"):
            await asyncio.to_thread(provider.start)
        app.state.boot.update(stage="PREPARING MARKET DASHBOARD", step=3)
        log.info("[SEED 3/3] Building first dashboard snapshot")
        # In ticker mode this processes the initial REST quote cache, without
        # waiting for an upstream WebSocket tick. During market close this is fine.
        await asyncio.to_thread(engine.update)
        if engine.snapshot().get("status") != "ok":
            raise RuntimeError("No usable market data available from Kite")
        app.state.engine = engine
        app.state.boot.update(stage="READY", step=3, status="ok", error=None)
        log.info("[SEED READY] %d stocks loaded", len(engine.snapshot().get("stocks", [])))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Do not leak API credentials or query strings into public API responses.
        log.exception("Kite background seeding failed")
        app.state.boot.update(
            stage="SEEDING FAILED", status="error", step=0,
            error=f"Kite initialization failed ({type(exc).__name__}). Check access token and Render logs."
        )
        if engine is not None and hasattr(engine.provider, "stop"):
            try:
                await asyncio.to_thread(engine.provider.stop)
            except Exception:
                log.exception("Could not stop failed ticker connection")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = None
    app.state.clients = set()
    app.state.chart_cache = {}
    app.state.chart_cache_lock = asyncio.Lock()
    app.state.boot = {
        "status": "starting", "stage": "SERVER ONLINE / PREPARING SEED", "step": 0,
        "started_at": now_ist().isoformat(), "error": None,
    }
    # No Kite SDK network calls before yield; Render can bind $PORT immediately.
    app.state.seed_task = asyncio.create_task(seed_market(app), name="kite-seed")
    app.state.refresh_task = asyncio.create_task(refresh_loop(app), name="neon-dashboard")
    try:
        yield
    finally:
        for task in (app.state.seed_task, app.state.refresh_task):
            task.cancel()
        await asyncio.gather(app.state.seed_task, app.state.refresh_task, return_exceptions=True)
        engine = app.state.engine
        if engine is not None and hasattr(engine.provider, "stop"):
            await asyncio.to_thread(engine.provider.stop)


app = FastAPI(title="NEONFLOW — Kite Market Map", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


async def refresh_loop(app: FastAPI):
    while True:
        engine = app.state.engine
        if engine is not None:
            try:
                # Ticker mode reads the in-memory cache; REST mode queries Kite.
                await asyncio.to_thread(engine.update)
            except Exception as exc:
                log.exception("Market refresh failed")
                engine.fail(f"{type(exc).__name__}: {exc}")
        data = current_state(app)
        broken = []
        for ws in tuple(app.state.clients):
            try:
                await ws.send_json(data)
            except Exception:
                broken.append(ws)
        for ws in broken:
            app.state.clients.discard(ws)
        await asyncio.sleep(POLL_SECONDS)


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/state")
async def state():
    return current_state(app)


@app.get("/healthz")
async def healthz():
    """Render liveness check: fast even during slow Kite API calls."""
    return {"status": "online", "market_status": current_state(app)["status"]}


@app.websocket("/ws")
async def websocket_state(ws: WebSocket):
    await ws.accept()
    app.state.clients.add(ws)
    try:
        await ws.send_json(current_state(app))
        # This is a read-only live-feed channel; accept incoming pings.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        app.state.clients.discard(ws)


@app.get("/api/chart/{symbol}")
async def stock_chart(symbol: str, timeframe: str = '5m'):
    """Server-only Kite OHLC history at a permitted interval, plus price-action blocks."""
    if timeframe not in CHART_INTERVALS:
        raise HTTPException(400, "Invalid timeframe. Use 1m, 3m, 5m, 15m, 30m or 1D")
    kite_interval, interval_seconds, lookback_days = CHART_INTERVALS[timeframe]
    symbol = symbol.upper().strip()
    if symbol not in ALL_SYMBOLS:
        raise HTTPException(404, "Stock not in configured NSE universe")
    engine = app.state.engine
    if engine is None:
        raise HTTPException(503, "Market data is still seeding. Try again shortly.")
    provider = engine.provider
    if engine.mode == 'live' and symbol not in provider.nse_instruments:
        raise HTTPException(404, f"No NSE instrument token for {symbol}")
    async with app.state.chart_cache_lock:
        cache_key = (symbol, timeframe)
        entry = app.state.chart_cache.get(cache_key)
        if entry and time.monotonic() - entry[0] < 45:
            return entry[1]
        now = now_ist()
        if engine.mode == 'demo':
            price = next((s['price'] for s in engine.snapshot()['stocks'] if s['symbol'] == symbol), None)
            candles = demo_candles(symbol, price, now, timeframe=timeframe)
        else:
            token = int(provider.nse_instruments[symbol]['instrument_token'])
            try:
                raw = await asyncio.to_thread(
                    provider.kite.historical_data, token,
                    now - timedelta(days=lookback_days), now, kite_interval, False, False,
                )
            except Exception as exc:
                log.warning("Kite history failed for %s: %s", symbol, type(exc).__name__)
                raise HTTPException(502,
                    "Kite could not provide requested NSE candles. Check your access token, historical-data subscription, and API limits.") from exc
            candles = normalize_candles(raw)
        if not candles:
            raise HTTPException(503, f"No {timeframe} candles available for {symbol}")
        closed = last_completed_count(candles, now, interval_seconds=interval_seconds)
        blocks = identify_order_blocks(candles, completed=closed)
        result = {
            'symbol': symbol, 'interval': kite_interval, 'timeframe': timeframe,
            'interval_seconds': interval_seconds, 'mode': engine.mode,
            'exchange': 'NSE', 'candles': candles, 'order_blocks': blocks,
            'completed_candles': closed, 'generated_at': now.isoformat(),
            'methodology': 'Last opposite candle before a confirmed strong 6-bar swing breakout. Zones are invalidated on a later completed close beyond the far edge. Heuristic, not proof of institutional orders.',
        }
        app.state.chart_cache[cache_key] = (time.monotonic(), result)
        # Cap per-session chart cache, including when many users inspect stocks.
        if len(app.state.chart_cache) > 80:
            oldest = min(app.state.chart_cache, key=lambda k: app.state.chart_cache[k][0])
            app.state.chart_cache.pop(oldest, None)
        return result
