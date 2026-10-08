"""Launch via `uvicorn main:app --host 127.0.0.1 --port 8000`."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from momentum20 import daily_baseline, demo_baselines
from proscore import compact_history, session_date
from stream5m import LiveFiveMinuteCache, bucket_for
from charting import last_completed_count, normalize_candles
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from market import DemoProvider, LiveKiteProvider, KiteTickerProvider, MarketEngine, now_ist
from charting import demo_candles, identify_order_blocks, CHART_INTERVALS
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
        if isinstance(provider, KiteTickerProvider):
            # Install the fast, thread-safe candle listener before connecting.
            provider.add_tick_listener(app.state.stream5m.accept)
            provider.add_disconnect_listener(app.state.stream5m.on_disconnect)
            engine.stream_5m = app.state.stream5m
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


async def paced_history(app, provider, token, start, end, interval):
    """Coordinate daily and intraday Kite requests to avoid concurrent bursts."""
    if not hasattr(app.state, 'history_lock'):
        return await asyncio.to_thread(provider.kite.historical_data,token,start,end,interval,False,False)
    async with app.state.history_lock:
        wait=max(0.0,0.55-(time.monotonic()-app.state.history_last))
        if wait:await asyncio.sleep(wait)
        app.state.history_last=time.monotonic()
        return await asyncio.to_thread(provider.kite.historical_data,token,start,end,interval,False,False)


async def seed_momentum20(app: FastAPI):
    """20-day history loads after website / market quotes are ready; never blocks HTTP."""
    last_seed_day = None
    while True:
        engine = app.state.engine
        if engine is None:
            await asyncio.sleep(1)
            continue
        today = now_ist().date()
        if last_seed_day == today:
            await asyncio.sleep(120)
            continue
        last_seed_day = today
        engine.reset_momentum20()
        try:
            if engine.mode == 'demo':
                for symbol, avg in demo_baselines(set(ALL_SYMBOLS)).items():
                    engine.set_momentum20_baseline(symbol, avg)
                engine.set_momentum20_progress(status='ready', processed=len(ALL_SYMBOLS), available=len(ALL_SYMBOLS), total=len(ALL_SYMBOLS))
                log.info('20D simulated baselines seeded for %d instruments', len(ALL_SYMBOLS))
                await asyncio.sleep(120)
                continue
            symbols = sorted(engine.provider.nse_instruments)
            total = len(symbols)
            engine.set_momentum20_progress(status='loading', processed=0, available=0, total=total)
            available = 0
            for i, symbol in enumerate(symbols,1):
                token = int(engine.provider.nse_instruments[symbol]['instrument_token'])
                try:
                    now = now_ist()
                    # ~60 calendar days safely covers 21 past sessions plus holidays.
                    raw = await paced_history(app,engine.provider, token,
                        now-timedelta(days=65),now,'day')
                    avg = daily_baseline(raw, today)
                    if avg is not None:
                        engine.set_momentum20_baseline(symbol,avg)
                        available += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning('20D history unavailable for %s: %s',symbol,type(exc).__name__)
                engine.set_momentum20_progress(status='loading', processed=i, available=available, total=total)
                # Sequential request pacing to avoid triggering Kite historical-data rate limits.
                if i<total:
                    await asyncio.sleep(.7)
            engine.set_momentum20_progress(status='ready' if available else 'unavailable',processed=total,available=available,total=total)
            log.info('20D baselines completed: %d/%d instruments', available,total)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('20D history background loading failed')
            engine.set_momentum20_progress(status='unavailable',processed=0,available=0,total=len(ALL_SYMBOLS))


async def seed_proscore_polling(app: FastAPI):
    """5m bar-driven score seeding. Never blocks Render startup or web responses.

    Initial REST history is compacted to 20 prior cumulative-volume curves +
    EMA warmup. Thereafter request only current session. Sequential/paced to
    respect broker limits. Missing source data stays unavailable, not synthetic.
    """
    previous_day = None
    next_refresh = 0
    while True:
        engine = app.state.engine
        if engine is None:
            await asyncio.sleep(2)
            continue
        now = now_ist()
        day = now.date()
        # Wait for an actual completed bar (09:20 IST first). At market close
        # preserve the day's last computed score; don't endlessly re-query.
        if engine.mode == 'live' and (now.weekday()>=5 or (now.hour,now.minute)<(9,20) or ((now.hour,now.minute)>(15,35) and previous_day==day)):
            await asyncio.sleep(60)
            continue
        bar_key = (day, max(0, (now.hour*60+now.minute-555-1)//5))
        if previous_day == day and bar_key == next_refresh:
            await asyncio.sleep(5)
            continue
        previous_day = day
        next_refresh = bar_key
        symbols = sorted(engine.provider.nse_instruments) if engine.mode == 'live' else sorted(ALL_SYMBOLS)
        total = len(symbols)
        engine.set_pro_progress(status='loading', processed=0,total=total,available=0)
        available = 0
        for i,symbol in enumerate(symbols,1):
            try:
                current = now_ist()
                if engine.mode=='demo':
                    # Demo candles are synthetic; the demo baseline is synthetic too.
                    rows = demo_candles(symbol, None, current, timeframe='5m')
                    bars = rows[:last_completed_count(rows, current, interval_seconds=300)]
                else:
                    token=int(engine.provider.nse_instruments[symbol]['instrument_token'])
                    if symbol not in engine.pro_history or engine.pro_history[symbol].get('date') != current.date():
                        # 45 calendar days => normally >=20 full trading sessions.
                        start=current-timedelta(days=45)
                    else:
                        start=current-timedelta(days=2)
                    raw=await paced_history(app,engine.provider,token,start,current,'5minute')
                    rows=normalize_candles(raw)
                    bars=rows[:last_completed_count(rows,current,interval_seconds=300)]
                today=[b for b in bars if session_date(b)==current.date()]
                if not today:
                    continue
                old=engine.pro_history.get(symbol)
                if old and old.get('reference') and old.get('warmup') and old.get('date')==current.date():
                    context={'reference':old['reference'],'warmup':old['warmup'],'today':today,'date':current.date()}
                else:
                    c=compact_history(bars,current.date())
                    context={**c,'today':today,'date':current.date()}
                engine.set_pro_history(symbol,context)
                if engine.pro_scores.get(symbol,{}).get('score') is not None:
                    available+=1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Pro 5m history unavailable for %s: %s',symbol,type(exc).__name__)
            finally:
                engine.set_pro_progress(status='loading',processed=i,total=total,available=available)
                # One request at a time; avoid bursts with chart endpoint and daily history.
                if engine.mode=='live' and i<total:
                    await asyncio.sleep(.7)
                elif engine.mode=='demo' and i % 25==0:
                    await asyncio.sleep(0)
        engine.set_pro_progress(status='ready' if available else 'unavailable',processed=total,total=total,available=available)
        # Leave time for the next completed five-minute block.
        await asyncio.sleep(20)




async def seed_proscore_stream(app: FastAPI):
    """Only one full 5m history seed per stock per IST session.

    Never fetch that full lookback on the regular 5m score cadence. The
    WebSocket cache extends the seeded session candles after this point.
    """
    seeded_day = None
    while True:
        engine = app.state.engine
        if engine is None:
            await asyncio.sleep(1)
            continue
        now = now_ist()
        day = now.date()
        if now.weekday() >= 5 or seeded_day == day:
            await asyncio.sleep(30)
            continue
        # Pre-opening seed is allowed, but don't seed during midnight maintenance.
        if (now.hour, now.minute) < (8, 45):
            await asyncio.sleep(60)
            continue
        seeded_day = day
        provider = engine.provider
        cache = app.state.stream5m
        if cache.day != day:
            cache.reset(day)
        symbols = sorted(provider.nse_instruments)
        total = len(symbols)
        engine.set_pro_progress(status='loading', processed=0, total=total,
                                available=0, source='kite_websocket', session=str(day))
        available = 0
        for i, symbol in enumerate(symbols, 1):
            try:
                now = now_ist()
                # 45 days usually covers >20 complete NSE sessions. No
                # fabricated reference if fewer days are returned.
                token = int(provider.nse_instruments[symbol]['instrument_token'])
                raw = await paced_history(app, provider, token,
                                          now - timedelta(days=48), now, '5minute')
                rows = normalize_candles(raw)
                bars = rows[:last_completed_count(rows, now, interval_seconds=300)]
                completed_today = [bar for bar in bars if session_date(bar) == day]
                context = {**compact_history(bars, day),
                           'today': completed_today, 'date': day}
                cache.seed(symbol, completed_today, now)
                # Some ticks might have arrived during the Kite HTTP request;
                # prefer broker candles for past closed bars and newer live bars.
                context['today'] = cache.bars(symbol)
                engine.set_pro_history(symbol, context)
                if engine.pro_scores.get(symbol, {}).get('score') is not None:
                    available += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Initial 5m seed failed for %s: %s', symbol, type(exc).__name__)
            finally:
                engine.set_pro_progress(status='loading', processed=i, total=total,
                                        available=available, source='kite_websocket',
                                        session=str(day))
                if i < total:
                    await asyncio.sleep(.7)
        engine.set_pro_progress(status='ready' if available else 'unavailable',
                                processed=total, total=total, available=available,
                                source='kite_websocket', session=str(day),
                                phase='STREAMING / CLOSED 5M CANDLES')
        log.info('[V14] One-time 5m seed completed: %d scores / %d instruments', available, total)
        await asyncio.sleep(10)


async def score_from_tick_candles(app: FastAPI):
    """No Kite HTTP calls: score newly closed bars from the live candle cache."""
    last_score_boundary = None
    while True:
        await asyncio.sleep(1)
        engine = app.state.engine
        if engine is None or not isinstance(engine.provider, KiteTickerProvider):
            continue
        now = now_ist()
        cache = app.state.stream5m
        changed = cache.close_due(now)
        if changed:
            pending = cache.pending_recovery()
            with engine.lock:
                for symbol in changed - pending:
                    context = engine.pro_history.get(symbol)
                    if not context or context.get('date') != now.date():
                        continue
                    bars = cache.bars(symbol)
                    if not bars:
                        continue
                    if context['today'] and bars[-1]['time'] <= context['today'][-1]['time']:
                        continue
                    updated = dict(context, today=bars)
                    engine.set_pro_history(symbol, updated)
        bucket = bucket_for(now)
        if bucket is None:
            continue
        # Only update status and available score counts once per completed 5m
        # boundary, not for every incoming tick packet.
        boundary = bucket if now.timestamp() >= bucket else bucket - 300
        if last_score_boundary != boundary and now.timestamp() >= boundary:
            last_score_boundary = boundary
            available = sum(1 for s in engine.pro_scores.values() if s.get('score') is not None)
            seed_state = dict(engine.pro_progress)
            if seed_state['status'] in ('ready', 'unavailable'):
                engine.set_pro_progress(available=available,
                    last_closed_at=cache.info()['last_closed_at'],
                    phase='STREAMING / CLOSED 5M CANDLES',
                    pending_recovery=cache.info()['pending_recovery'])


async def recover_tick_gaps(app: FastAPI):
    """Paced, bounded historical gap repair only; never periodic full polling."""
    attempted = {}
    while True:
        await asyncio.sleep(8)
        engine = app.state.engine
        if engine is None or not isinstance(engine.provider, KiteTickerProvider):
            continue
        now = now_ist()
        if bucket_for(now) is None or not engine.provider.connected:
            continue
        cache = app.state.stream5m
        for symbol in sorted(cache.pending_recovery()):
            now = now_ist()
            # Avoid hammering the API if an illiquid stock has no trades.
            if time.monotonic() - attempted.get(symbol, -1e9) < 300:
                continue
            attempted[symbol] = time.monotonic()
            ctx = engine.pro_history.get(symbol)
            row = engine.provider.nse_instruments.get(symbol)
            if not ctx or not row or ctx.get('date') != now.date():
                continue
            if not cache.recovery_ready(symbol, now):
                continue
            try:
                raw = await paced_history(app, engine.provider,
                    int(row['instrument_token']), now - timedelta(days=1), now, '5minute')
                bars = normalize_candles(raw)
                closed = bars[:last_completed_count(bars, now, interval_seconds=300)]
                today = [b for b in closed if session_date(b) == now.date()]
                if not today:
                    continue
                repaired = cache.recovered(symbol, today, now)
                if repaired:
                    engine.set_pro_history(symbol, dict(ctx, today=cache.bars(symbol)))
                    log.info('[RECOVERY] Reconciled %s 5m candles from Kite history', symbol)
                else:
                    log.info('[RECOVERY] %s still has missing 5m candles; score held', symbol)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('[RECOVERY] %s failed: %s', symbol, type(exc).__name__)
            await asyncio.sleep(.7)


async def seed_proscore(app: FastAPI):
    """WebSocket tick mode seeds once; REST/demo retain legacy update behavior."""
    while app.state.engine is None:
        await asyncio.sleep(1)
    if isinstance(app.state.engine.provider, KiteTickerProvider):
        await seed_proscore_stream(app)
    else:
        await seed_proscore_polling(app)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = None
    app.state.clients = set()
    app.state.chart_cache = {}
    app.state.chart_cache_lock = asyncio.Lock()
    app.state.history_lock=asyncio.Lock()
    app.state.history_last=0.0
    app.state.stream5m = LiveFiveMinuteCache()
    app.state.boot = {
        "status": "starting", "stage": "SERVER ONLINE / PREPARING SEED", "step": 0,
        "started_at": now_ist().isoformat(), "error": None,
    }
    # No Kite SDK network calls before yield; Render can bind $PORT immediately.
    app.state.seed_task = asyncio.create_task(seed_market(app), name="kite-seed")
    app.state.refresh_task = asyncio.create_task(refresh_loop(app), name="neon-dashboard")
    app.state.momentum_task = asyncio.create_task(seed_momentum20(app), name="kite-20d-history")
    app.state.pro_task = asyncio.create_task(seed_proscore(app), name="kite-pro-score-seed-once")
    app.state.score_task = asyncio.create_task(score_from_tick_candles(app), name="kite-score-from-live-ticks")
    app.state.repair_task = asyncio.create_task(recover_tick_gaps(app), name="kite-5m-gap-recovery")
    try:
        yield
    finally:
        tasks=(app.state.seed_task, app.state.refresh_task, app.state.momentum_task,
               app.state.pro_task, app.state.score_task, app.state.repair_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
            context = getattr(engine, 'pro_history', {}).get(symbol)
            if (timeframe == '5m' and isinstance(provider, KiteTickerProvider)
                    and context and context.get('date') == now.date()):
                # The one-time 5m seed already contains EMA warmup + today;
                # chart opens must not re-query Kite for identical history.
                candles = list(context.get('warmup') or []) + app.state.stream5m.bars(symbol)
            else:
                if timeframe == '5m' and isinstance(provider, KiteTickerProvider):
                    raise HTTPException(503, '5-minute history is still seeding in the background for this symbol.')
                # Other user-selected chart timeframes are fetched on demand.
                token = int(provider.nse_instruments[symbol]['instrument_token'])
                try:
                    raw = await paced_history(app, provider, token,
                        now - timedelta(days=lookback_days), now, kite_interval)
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
