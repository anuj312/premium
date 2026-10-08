"""Deterministic bar-stream tests; no Zerodha credentials needed."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import asyncio
from types import SimpleNamespace

from stream5m import LiveFiveMinuteCache, bucket_for
from main import stock_chart, app
from charting import demo_candles

IST = ZoneInfo('Asia/Kolkata')
DAY = datetime(2026, 10, 8, 9, 15, tzinfo=IST)


def dt(minute=0, second=0):
    return DAY + timedelta(minutes=minute, seconds=second)


def tick(cache, at, price, cum, symbol='INFY'):
    cache.accept(f'NSE:{symbol}', {'last_price':price, 'volume_traded':cum}, at)


def test_nse_time_bucketing_anchored_to_915():
    assert bucket_for(dt(0)) == int(dt(0).timestamp())
    assert bucket_for(dt(4,59)) == int(dt(0).timestamp())
    assert bucket_for(dt(5)) == int(dt(5).timestamp())
    assert bucket_for(dt(374,59)) == int(dt(370).timestamp())
    assert bucket_for(dt(375)) is None
    assert bucket_for(dt(-1)) is None


def test_cumulative_volume_yields_only_closed_5m_bars():
    stream=LiveFiveMinuteCache()
    stream.seed('INFY', [], dt(0))
    tick(stream,dt(0,8),100,50)
    tick(stream,dt(2),101,80)
    assert stream.bars('INFY') == []
    assert stream.close_due(dt(4,59)) == set()
    assert stream.close_due(dt(5)) == {'INFY'}
    bar=stream.bars('INFY')[0]
    assert bar == {'time':int(dt().timestamp()), 'open':100,'high':101,
                   'low':100,'close':101,'volume':80}
    # Delta is against previous *cumulative* session volume (80), not LTQ.
    tick(stream,dt(5,2),102,90)
    tick(stream,dt(7),99,160)
    stream.close_due(dt(10))
    bars=stream.bars('INFY')
    assert len(bars)==2
    assert bars[-1]['volume']==80 and bars[-1]['low']==99
    assert stream.info()['closed_tick_candles']==2


def test_mid_session_partial_bucket_is_not_scored_or_synthesized():
    stream=LiveFiveMinuteCache()
    stream.seed('INFY', [], dt(60))
    tick(stream,dt(60,10),110,15000)
    stream.close_due(dt(65))
    assert stream.bars('INFY')==[]
    assert stream.pending_recovery()=={'INFY'}
    tick(stream,dt(65,5),111,15120)
    stream.close_due(dt(70))
    assert len(stream.bars('INFY'))==1
    assert stream.bars('INFY')[0]['volume']==120
    # Without authoritative history for the missing bar, it remains flagged.
    assert not stream.recovered('INFY',[],dt(71))
    assert stream.pending_recovery()=={'INFY'}
    assert stream.recovered('INFY', [{'time':int(dt(60).timestamp()),'open':110,'high':111,
                                     'low':109,'close':110.5,'volume':400}], dt(72))
    assert not stream.pending_recovery()
    assert len(stream.bars('INFY'))==2


def test_disconnect_gap_requires_reconciliation_and_never_guesses_zero_volume():
    stream=LiveFiveMinuteCache()
    stream.seed('INFY', [], dt(0))
    tick(stream,dt(1),100,300)
    stream.on_disconnect()
    assert stream.pending_recovery()=={'INFY'}
    stream.close_due(dt(5))
    assert not stream.bars('INFY')
    tick(stream,dt(15),103,750)
    stream.close_due(dt(20))
    assert not stream.recovered('INFY', [], dt(21))
    assert stream.pending_recovery()=={'INFY'}
    assert stream.info()['disconnects']==1


def test_future_ticks_do_not_construct_cash_candles():
    stream=LiveFiveMinuteCache()
    stream.accept('NFO:INFY26OCTFUT', {'last_price':1550, 'volume_traded':200}, dt(1))
    assert stream.info()['closed_tick_candles']==0
    assert not stream.in_progress


def test_opening_five_minute_chart_reuses_seed_cache_without_kite_request(monkeypatch):
    import main
    from stream5m import LiveFiveMinuteCache
    class NeverCallKite:
        def historical_data(self,*args,**kwargs):
            raise AssertionError('5m chart must read seeded memory, not Kite history')
    from market import KiteTickerProvider
    provider=object.__new__(KiteTickerProvider)
    provider.nse_instruments={'INFY':{'instrument_token':101}}
    provider.kite=NeverCallKite()
    cache=LiveFiveMinuteCache()
    bars=[{'time':int(dt().timestamp()),'open':100,'high':102,'low':99,'close':101,'volume':1000}]
    cache.seed('INFY',bars,dt(10))
    engine=SimpleNamespace(mode='live',provider=provider,
         pro_history={'INFY':{'date':dt(0).date(),'warmup':[],'today':bars}})
    old_engine=getattr(app.state,'engine',None)
    old_stream=getattr(app.state,'stream5m',None)
    old_cache=getattr(app.state,'chart_cache',None)
    old_lock=getattr(app.state,'chart_cache_lock',None)
    monkeypatch.setattr(main,'now_ist',lambda:dt(10))
    try:
        app.state.engine=engine
        app.state.stream5m=cache
        app.state.chart_cache={}
        app.state.chart_cache_lock=asyncio.Lock()
        result=asyncio.run(stock_chart('INFY','5m'))
        assert len(result['candles'])==1
        assert result['candles'][0]['close']==101
    finally:
        app.state.engine=old_engine
        app.state.stream5m=old_stream
        app.state.chart_cache=old_cache
        app.state.chart_cache_lock=old_lock


def test_one_time_session_seed_does_not_poll_again_at_five_minute_boundaries(monkeypatch):
    """One instrument seeds once; the persistent loop sleeps, not re-queries."""
    import main
    from market import MarketEngine
    rows=[]
    for b in demo_candles('INFY',100,dt(75),timeframe='5m'):
        rows.append({'date':datetime.fromtimestamp(b['time'],IST),
                     'open':b['open'],'high':b['high'],'low':b['low'],
                     'close':b['close'],'volume':b['volume']})
    provider=SimpleNamespace(nse_instruments={'INFY':{'instrument_token':21}})
    engine=MarketEngine(provider,'live')
    engine._snapshot = {'stocks':[{'symbol':'INFY','sector':'IT','prev_close':100,
                                   'futures_price_connect_pct':None,'oi_change_pct':None}],
                        'sectors':[{'name':'IT','change_pct':1}]}
    engine.set_momentum20_baseline('INFY',1.0)
    app_like=SimpleNamespace(state=SimpleNamespace(engine=engine,
                stream5m=LiveFiveMinuteCache()))
    calls=[]
    async def fake_paced(*args):
        calls.append(args[2])
        return rows
    monkeypatch.setattr(main,'now_ist',lambda:dt(75))
    monkeypatch.setattr(main,'paced_history',fake_paced)

    async def run():
        task=asyncio.create_task(main.seed_proscore_stream(app_like))
        try:
            for _ in range(100):
                if engine.pro_progress.get('status') in ('ready','unavailable'):
                    break
                await asyncio.sleep(.01)
            assert engine.pro_progress['processed'] == 1
            assert app_like.state.stream5m.info()['seeded']==1
            # No periodic polling: after the seed completes it sleeps; even
            # checking the task again does not issue another historical fetch.
            await asyncio.sleep(.05)
            assert len(calls)==1
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(run())


def test_failed_recovery_does_not_hide_a_missing_bar():
    stream=LiveFiveMinuteCache()
    stream.seed('INFY', [], dt(0))
    tick(stream,dt(1),100,100)
    tick(stream,dt(16),104,400)  # 09:20 and 09:25 missing
    assert stream.pending_recovery()=={'INFY'}
    # Fetch from Kite omits no-trade/missing intervals => still flagged.
    assert not stream.recovered('INFY', [], dt(17))
    assert stream.pending_recovery()=={'INFY'}
