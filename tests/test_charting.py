import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from charting import normalize_candles, identify_order_blocks, last_completed_count, demo_candles

IST=ZoneInfo('Asia/Kolkata')

def sample():
    first=datetime(2026,10,7,9,15,tzinfo=IST)
    rows=[]
    for i in range(11):
        rows.append({'date':first+timedelta(minutes=i*5), 'open':100.3, 'high':100.8,
                     'low':99.7, 'close':100.1, 'volume':1000})
    rows.append({'date': first+timedelta(minutes=55), 'open':100.4,'high':101.1,'low':99.6,'close':99.9,'volume':1100})  # origin
    rows.append({'date': first+timedelta(minutes=60), 'open':100.0,'high':102.9,'low':100.0,'close':102.7,'volume':10000})  # breakout
    return normalize_candles(rows)


def test_confirm_uses_only_completed_candles_and_next_bar():
    candles=sample()
    assert identify_order_blocks(candles,completed=12)==[]
    zones=identify_order_blocks(candles,completed=13)
    bulls=[b for b in zones if b['type']=='bullish']
    assert len(bulls)==1
    assert bulls[0]['low']==99.6 and bulls[0]['high']==100.4
    assert bulls[0]['status']=='fresh'
    assert bulls[0]['confirmed_time']==candles[12]['time']


def test_zone_invalidation_requires_close_not_wick():
    candles=sample()
    wick={**candles[-1], 'time':candles[-1]['time']+300, 'open':102.7, 'high':103.0,'low':99.3,'close':101.0}
    assert len([z for z in identify_order_blocks(candles+[wick]) if z['type']=='bullish']) == 1
    invalid={**wick,'time':wick['time']+300, 'open':100.4,'high':100.8,'low':98.9,'close':99.4}
    assert not [z for z in identify_order_blocks(candles+[wick,invalid]) if z['type']=='bullish']


def test_kite_aware_timezone_and_demo():
    ts=datetime(2026,10,8,9,15,tzinfo=IST)
    normalized=normalize_candles([{'date':ts,'open':10,'high':12,'low':9,'close':11,'volume':123}])
    assert normalized[0]['time']==int(ts.timestamp())
    assert normalize_candles([{'date':ts,'open':-1,'high':1,'low':.5,'close':.7}])==[]
    demo=demo_candles('MPHASIS',1000,datetime(2026,10,8,11,20,tzinfo=IST))
    assert len(demo)>300
    assert last_completed_count(demo,datetime(2026,10,8,11,20,tzinfo=IST))==len(demo)-1
    assert demo[-1]['close']==1000


def test_live_history_from_kite_never_uses_demo_fallback():
    """Exercise the live chart handler with a local mocked Kite SDK."""
    import asyncio
    from types import SimpleNamespace
    from main import app, stock_chart
    from fastapi import HTTPException
    class MockKite:
        def __init__(self): self.requests=[]
        def historical_data(self, token, date_from, date_to, interval, continuous, oi):
            self.requests.append((token,interval,continuous,oi))
            return [
                {'date':datetime(2026,10,7,9,15,tzinfo=IST),'open':100,'high':102,'low':99,'close':101,'volume':3000},
                {'date':datetime(2026,10,7,9,20,tzinfo=IST),'open':101,'high':103,'low':100,'close':102,'volume':4500},
            ]
    kite=MockKite()
    original=getattr(app.state,'engine',None)
    try:
        app.state.engine=SimpleNamespace(mode='live',provider=SimpleNamespace(nse_instruments={'MPHASIS':{'instrument_token':424242}},kite=kite))
        app.state.chart_cache={}
        app.state.chart_cache_lock=asyncio.Lock()
        payload=asyncio.run(stock_chart('MPHASIS'))
        assert payload['mode']=='live' and payload['exchange']=='NSE'
        assert len(payload['candles'])==2 and payload['candles'][0]['volume']==3000
        assert kite.requests==[(424242,'5minute',False,False)]
        # Cached result doesn't burn another Kite request.
        assert asyncio.run(stock_chart('MPHASIS'))==payload
        assert len(kite.requests)==1
    finally:
        if original is None: del app.state.engine
        else: app.state.engine=original
