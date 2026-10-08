"""Professional 0-100 score: no lookahead, 20 sessions, direction, missing sources."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from proscore import WEIGHTS, build_volume_reference, compact_history, evaluate

IST=ZoneInfo('Asia/Kolkata')

def candle(day, slot, close, volume=100, previous=None):
    stamp=datetime.combine(day,time(9,15),IST)+timedelta(minutes=5*slot)
    op=previous if previous is not None else close
    return {'time':int(stamp.timestamp()),'open':op,'high':max(op,close)+.03,
            'low':min(op,close)-.03,'close':close,'volume':volume}

def sample_history():
    start=date(2026,8,31)
    days=[]
    for n in range(40):
        d=start+timedelta(days=n)
        if d.weekday()<5:days.append(d)
    days=days[:22]
    previous=[]
    for day in days[:21]:
        previous += [candle(day,j,100+j*.01,volume=100) for j in range(75)]
    today=days[21]
    bullish=[candle(today,j,100+(j+1)*.30,volume=200,previous=100+j*.30) for j in range(16)]
    bearish=[candle(today,j,100-(j+1)*.30,volume=200,previous=100-j*.30) for j in range(16)]
    return previous, bullish, bearish,today


def test_weights_and_time_matched_20day_volume_no_today_leakage():
    assert sum(WEIGHTS.values())==100
    prior,bull,bear,today=sample_history()
    old=compact_history(prior,today)
    ref=old['reference']
    assert len(ref)==75
    assert ref[15]==1600  # 16 bars * 100 vs today 16 * 200
    # Future volume today never enters the past baseline.
    assert build_volume_reference(prior+[dict(b,volume=999999) for b in bull],today)==ref
    score=evaluate(bull,old['warmup'],ref,1.0,100,oi_price_delta=2,oi_change_delta=3,sector_change=1)
    assert score['rvol']==2.0
    assert score['score'] is not None and 0<=score['score']<=100
    assert score['direction']=='bullish'
    assert score['persistence']=={'15':True,'30':True,'60':True}
    assert score['efficiency_pct']==100
    assert score['components']['oi']>0
    assert score['coverage']==100


def test_bearish_direction_and_futures_oi_aligned():
    prior,bull,bear,today=sample_history()
    old=compact_history(prior,today)
    short=evaluate(bear,old['warmup'],old['reference'],1,100,
                   oi_price_delta=-2,oi_change_delta=3,sector_change=-1)
    wrong=evaluate(bear,old['warmup'],old['reference'],1,100,
                   oi_price_delta=2,oi_change_delta=3,sector_change=-1)
    assert short['direction']=='bearish'
    assert short['score'] > wrong['score']
    assert wrong['components']['oi']==0
    assert short['components']['oi']>0
    assert short['components']['sector']>0


def test_missing_history_missing_oi_and_early_session_never_fake_score():
    prior,bull,bear,today=sample_history()
    old=compact_history(prior,today)
    assert evaluate(bull,old['warmup'],None,1,100)['score'] is None
    assert evaluate(bull,old['warmup'],old['reference'],None,100)['score'] is None
    assert evaluate(bull[:6],old['warmup'],old['reference'],1,100)['score'] is None
    nooi=evaluate(bull,old['warmup'],old['reference'],1,100,
                  oi_price_delta=None,oi_change_delta=None,sector_change=1)
    assert 'OI UNAVAILABLE' in nooi['flags']
    assert nooi['components']['oi']==0
    assert nooi['coverage']==90


def test_choppy_trend_efficiency_below_oneway():
    prior,bull,bear,today=sample_history()
    old=compact_history(prior,today)
    vals=[100.3,99.9,100.6,100.2,100.9,100.5,101.2,100.8,
          101.5,101.1,101.8,101.4,102.1,101.7,102.4,104.8]
    choppy=[candle(today,i,c,volume=200,previous=vals[i-1] if i else 100) for i,c in enumerate(vals)]
    smooth=evaluate(bull,old['warmup'],old['reference'],1,100)
    noisy=evaluate(choppy,old['warmup'],old['reference'],1,100)
    assert smooth['efficiency_pct']>noisy['efficiency_pct']
    assert smooth['components']['trend']>noisy['components']['trend']
