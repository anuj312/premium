"""Explainable, directional 0-100 intraday score; only completed NSE 5m candles.

The 20-session volume reference is time-of-day matched cumulative traded volume.
Order-flow/institutional activity is NOT inferred from these scores.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
WEIGHTS = {'momentum':20,'rvol':20,'trend':20,'vwap':10,'ema50':10,
           'oi':10,'sector':5,'breakout':5}


def session_date(bar):
    return datetime.fromtimestamp(bar['time'],IST).date()


def session_slot(bar):
    t=datetime.fromtimestamp(bar['time'],IST)
    return (t.hour*60+t.minute-555)//5  # NSE 09:15 opening bar => 0


def build_volume_reference(history, today):
    """Use 20 *completed* prior sessions; exclude missing/partial sessions.

    Every session must have at least 60 regular-session bars and include 09:15.
    Return mean cumulative volume per 5-minute slot; no future leakage.
    """
    dates=defaultdict(dict)
    for b in history:
        day=session_date(b)
        slot=session_slot(b)
        if day<today and 0<=slot<75:
            dates[day][slot]=max(0,int(b.get('volume') or 0))
    complete=[]
    for day in sorted(dates):
        slots=dates[day]
        if len(slots)>=60 and 0 in slots and max(slots)>=70:
            total=0
            curve={}
            for slot in sorted(slots):
                total+=slots[slot]
                curve[slot]=total
            complete.append(curve)
    if len(complete)<20:
        return None
    curves=complete[-20:]
    reference={}
    for slot in range(75):
        vals=[curve[slot] for curve in curves if slot in curve]
        if len(vals)==20:
            reference[slot]=mean(vals)
    return reference


def compact_history(candles,today):
    """Only cache reference curve and EMA warm-up bars, not all 20 days."""
    prior=[c for c in candles if session_date(c)<today]
    return {'reference':build_volume_reference(candles,today),'warmup':prior[-95:]}


def ema_at(closes, period=50):
    if len(closes)<period:return None,None
    v=mean(closes[:period]);alpha=2/(period+1)
    previous=v
    for p in closes[period:]:
        previous=v
        v=alpha*p+(1-alpha)*v
    return v, previous


def clamp(x,lo=0.0,hi=1.0):
    return max(lo,min(hi,x))


def evaluate(candles, warmup, reference, day_move_baseline, previous_close,
             oi_price_delta=None, oi_change_delta=None, sector_change=None):
    """Return a frozen score snapshot from the latest closed session candles.

    Futures OI points are awarded only when FUTSTK price AND OI are direction-aligned
    using the same since-connection basis. Missing values contribute 0 of 10;
    missing 20D baseline/reference produces score=None instead of a fake ranking.
    """
    result={'score':None,'direction':None,'components':{},'asof':None,
            'rvol':None,'efficiency_pct':None,'persistence':None,'ema50':None,
            'vwap':None,'momentum_x':None,'coverage':None,'flags':[],
            'reason':'WAITING FOR COMPLETED 5M HISTORY'}
    if not candles or not previous_close or previous_close<=0:return result
    today=session_date(candles[-1]); bars=[c for c in candles if session_date(c)==today]
    if not bars:return result
    last=bars[-1]
    result['asof']=last['time']+300
    delta=(last['close']/previous_close-1)*100
    side=1 if delta>0.02 else -1 if delta<-.02 else 0
    result['direction']='bullish' if side==1 else 'bearish' if side==-1 else 'neutral'
    if len(bars)<12:
        result['reason']='WAITING FOR 12 COMPLETED 5M BARS'
        return result
    if day_move_baseline is None or day_move_baseline<=0:
        result['reason']='20D DAILY BASELINE UNAVAILABLE'
        return result
    if not reference:
        result['reason']='20D SAME-TIME VOLUME BASELINE UNAVAILABLE'
        return result
    slot=session_slot(last)
    expected=reference.get(slot)
    if not expected or expected<=0:
        result['reason']='20D VOLUME COMPARISON UNAVAILABLE'
        return result
    daily_volume=sum(max(0,b.get('volume',0)) for b in bars)
    rv=daily_volume/expected
    series=warmup+bars
    e50,e_prev=ema_at([c['close'] for c in series])
    if e50 is None:
        result['reason']='EMA50 WARMUP UNAVAILABLE'
        return result
    typical=[((b['high']+b['low']+b['close'])/3,max(0,b.get('volume',0))) for b in bars]
    tv=sum(p*v for p,v in typical)
    total=sum(v for _,v in typical)
    vwap=tv/total if total else None
    if vwap is None:
        result['reason']='5M SESSION VOLUME UNAVAILABLE'
        return result
    result.update(rvol=round(rv,2),vwap=round(vwap,2),ema50=round(e50,2),momentum_x=round(delta/day_move_baseline,2))
    # Directional efficiency: net displacement / actual travelled 5m close-to-close path.
    lookback=bars[-13:]
    change_path=sum(abs(b['close']-a['close']) for a,b in zip(lookback,lookback[1:]))
    net=lookback[-1]['close']-lookback[0]['close']
    eff=clamp(abs(net)/change_path) if change_path>0 else 0
    result['efficiency_pct']=round(eff*100,1)
    persist={}
    for period, steps in ((15,3),(30,6),(60,12)):
        window=bars[-(steps+1):]
        if len(window)<steps+1:
            persist[str(period)]=None
        else:
            persist[str(period)]=side*(window[-1]['close']-window[0]['close'])>0
    result['persistence']=persist
    points={key:0.0 for key in WEIGHTS}
    points['momentum']=20*clamp(side*delta/day_move_baseline/3)
    points['rvol']=20*clamp((rv-.7)/2.8)
    good_persist=sum(bool(p) for p in persist.values())
    points['trend']=12*eff+8*good_persist/3
    # alignment rewards correct side, not distance alone; penalize extensions separately
    distance=side*(last['close']/vwap-1)*100
    points['vwap']=10*clamp(distance/.45) if side else 0
    ema_dist=side*(last['close']/e50-1)*100
    slope=side*(e50-e_prev)/e_prev*100 if e_prev else 0
    points['ema50']=6*clamp(ema_dist/.5)+4*clamp(slope/.06) if side else 0
    # OI source is FUTSTK price and OI sampled over the same connection window.
    oi_ready=oi_price_delta is not None and oi_change_delta is not None
    if oi_ready and side:
        if side*oi_price_delta>.02 and oi_change_delta>.02:
            points['oi']=10*clamp(oi_change_delta/2.0)
        elif side*oi_price_delta>.02 and oi_change_delta<-.02:
            points['oi']=3
    else:
        result['flags'].append('OI UNAVAILABLE' if not oi_ready else 'FLAT PRICE')
    if sector_change is not None:
        points['sector']=5*clamp(side*sector_change/1.5) if side else 0
    else:result['flags'].append('SECTOR UNAVAILABLE')
    previous=bars[-13:-1]
    if len(previous)==12 and side==1 and last['close']>max(b['high'] for b in previous):
        points['breakout']=5
    if len(previous)==12 and side==-1 and last['close']<min(b['low'] for b in previous):
        points['breakout']=5
    score=sum(points.values())
    # Risk guardrails: not momentum points; no claims of guaranteed trade quality.
    if slot>=12 and daily_volume<10000:
        result['flags'].append('LOW LIQUIDITY')
        score-=10
    extension=abs(last['close']/vwap-1)*100
    if extension>2.5:
        result['flags'].append('EXTENDED FROM VWAP')
        score-=min(12,4*(extension-2.5))
    result.update(score=round(clamp(score,0,100),1),components={k:round(v,1) for k,v in points.items()},
                  coverage=round((100-(0 if oi_ready else 10)-(0 if sector_change is not None else 5)),0),
                  reason='READY' if side else 'FLAT / NO DIRECTION')
    return result
