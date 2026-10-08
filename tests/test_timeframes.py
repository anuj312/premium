import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from charting import CHART_INTERVALS, demo_candles, last_completed_count, normalize_candles
from main import app, stock_chart

IST = ZoneInfo('Asia/Kolkata')


def candle_at(when):
    return {'time': int(when.timestamp()), 'open': 100, 'high': 102,
            'low': 99, 'close': 101, 'volume': 10000}


def test_validated_intervals_and_demo_shapes():
    assert set(CHART_INTERVALS) == {'1m', '3m', '5m', '15m', '30m', '1D'}
    now = datetime(2026, 10, 8, 11, 23, tzinfo=IST)
    for tf, (_, seconds, _) in CHART_INTERVALS.items():
        bars = demo_candles('MPHASIS', 1200, now, timeframe=tf)
        assert len(bars) >= 90
        assert abs(bars[-1]['close'] - 1200) < .01
        assert all(c['high'] >= max(c['open'], c['close']) and c['low'] <= min(c['open'],c['close']) for c in bars)
        assert 0 <= last_completed_count(bars, now, interval_seconds=seconds) <= len(bars)
    with pytest.raises(ValueError):
        demo_candles('MPHASIS', 1200, now, timeframe='2m')


def test_daily_completion_waits_until_market_close():
    day = datetime(2026, 10, 8, 0, 0, tzinfo=IST)
    c = candle_at(day)
    assert last_completed_count([c], datetime(2026, 10, 8, 12, 0, tzinfo=IST), 86400) == 0
    assert last_completed_count([c], datetime(2026, 10, 8, 15, 31, tzinfo=IST), 86400) == 1


def test_final_30_minute_candle_completed_at_session_close():
    c = candle_at(datetime(2026, 10, 8, 15, 15, tzinfo=IST))
    assert last_completed_count([c], datetime(2026, 10, 8, 15, 28, tzinfo=IST), 1800) == 0
    assert last_completed_count([c], datetime(2026, 10, 8, 15, 30, tzinfo=IST), 1800) == 1


def test_chart_timeframe_validation_and_separate_cache():
    class StubKite:
        def __init__(self): self.calls = []
        def historical_data(self, token, start, end, interval, continuous, oi):
            self.calls.append((token, interval, continuous, oi))
            t=datetime(2026, 10, 7, 9, 15, tzinfo=IST)
            return [{'date':t,'open':100,'high':102,'low':99,'close':101,'volume':10000}]
    kite=StubKite()
    engine=SimpleNamespace(mode='live', provider=SimpleNamespace(nse_instruments={'MPHASIS': {'instrument_token': 444}}, kite=kite))
    old_engine=getattr(app.state, 'engine', None)
    old_cache=getattr(app.state, 'chart_cache', None)
    old_lock=getattr(app.state, 'chart_cache_lock', None)
    try:
        app.state.engine=engine
        app.state.chart_cache={}
        app.state.chart_cache_lock=asyncio.Lock()
        a=asyncio.run(stock_chart('MPHASIS','1m'))
        b=asyncio.run(stock_chart('MPHASIS','1D'))
        assert a['interval']=='minute' and a['interval_seconds']==60
        assert b['interval']=='day' and b['interval_seconds']==86400
        assert len(app.state.chart_cache)==2
        assert asyncio.run(stock_chart('MPHASIS','1m'))==a
        assert kite.calls == [(444,'minute',False,False), (444,'day',False,False)]
        with pytest.raises(HTTPException) as e:
            asyncio.run(stock_chart('MPHASIS','45m'))
        assert e.value.status_code==400
    finally:
        if old_engine is None: del app.state.engine
        else: app.state.engine=old_engine
        if old_cache is None: del app.state.chart_cache
        else: app.state.chart_cache=old_cache
        if old_lock is None: del app.state.chart_cache_lock
        else: app.state.chart_cache_lock=old_lock
