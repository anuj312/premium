"""Conservative, explainable price-action order-block setup analysis.

NO trade-aggressor delta, institutional order flow, order-book depth, or guaranteed
liquidity is inferred. Only *completed* OHLCV candles are evaluated. Recent
BOS/displacement establishes a candidate; a completed retest/rejection candle
can make a candidate READY, subject to confirmed context and a score threshold.
The score is a heuristic display strength score, NOT win probability.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def _f(x, default=0.0):
    try:
        result = float(x)
        return result if result == result and abs(result) != float('inf') else default
    except (TypeError, ValueError, OverflowError):
        return default


def _timestamp(c):
    t = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


def _completed(candles, minutes, now):
    """Exclude last, still-building candle in any timeframe (including 1h at 9:15)."""
    bars = list(candles or [])
    if not bars:
        return bars
    latest = _timestamp(bars[-1])
    if latest <= now < latest + timedelta(minutes=minutes):
        bars.pop()
    return bars


def _trend_confirmation(candles_15, direction):
    if len(candles_15) < 6:
        return False
    cs = [b['close'] for b in candles_15[-6:]]
    return ((cs[-1] > sum(cs[-5:]) / 5 and cs[-1] > cs[-4]) if direction == 'bullish'
            else (cs[-1] < sum(cs[-5:]) / 5 and cs[-1] < cs[-4]))


def _fvg_near(candles, candidate_i, impulse_i, direction):
    """3-candle imbalance; only candles known on formation are considered."""
    for k in range(max(candidate_i + 2, impulse_i), min(len(candles), impulse_i + 3)):
        a, c = candles[k-2], candles[k]
        if direction == 'bullish' and _f(c['low']) > _f(a['high']):
            return {'fromIndex': k-2, 'toIndex': k, 'low': round(_f(a['high']), 2),
                    'high': round(_f(c['low']), 2), 'type': direction}
        if direction == 'bearish' and _f(c['high']) < _f(a['low']):
            return {'fromIndex': k-2, 'toIndex': k, 'low': round(_f(c['high']), 2),
                    'high': round(_f(a['low']), 2), 'type': direction}
    return None


def _sweep_before(candles, i, direction):
    # Sweeping an earlier local extreme AND closing back inside: wick by itself
    # is not sufficient. No pending market orders are implied.
    for j in range(max(5, i-3), i+1):
        prior = candles[max(0,j-6):j]
        if len(prior) < 4:
            continue
        b = candles[j]
        if direction == 'bullish':
            level = min(x['low'] for x in prior)
            if b['low'] < level and b['close'] > level:
                return {'index': j, 'price': round(level, 2), 'type': 'sell_side_sweep'}
        else:
            level = max(x['high'] for x in prior)
            if b['high'] > level and b['close'] < level:
                return {'index': j, 'price': round(level, 2), 'type': 'buy_side_sweep'}
    return None


def analyze_order_block(candles, *, timeframe='5m', candles15=None,
                        rvol20=None, demo=False, now=None, freshness_limit=36):
    """Return latest strict OB zone + bounded quality breakdown and setup state.

    Quality weights (sum 100): BOS + displacement 25, zone freshness 15,
    higher-timeframe direction 15, impulse volume 15, FVG 10,
    recent retest/rejection 10, sweep + session VWAP 10.
    No eligible zone => score None (not fabricated). Invalidated zones are
    capped at 25, and nonrecent retests cannot remain READY indefinitely.
    """
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    now = now.astimezone(IST)
    tf_minutes = {'5m':5,'15m':15,'30m':30,'1h':60}.get(timeframe)
    if tf_minutes is None:
        raise ValueError('Unsupported timeframe')
    base = {'obQualityScore':None, 'obState':'WAIT', 'obDirection':None,
            'obReason':'Waiting for completed candles and a confirmed structure break',
            'obBreakdown':{}, 'obZone':None, 'obFvg':None,
            'obSweep':None, 'obBos':None,
            'obBasis':'SIMULATED' if demo else 'CANDLE_DERIVED',
            'obDeltaSource':'UNAVAILABLE — no aggressor trade classification'}
    closed = _completed(candles, tf_minutes, now)
    if len(closed) < 12:
        return base
    # The signal must be in the most recent trading session, not an old zone
    # from yesterday that accidentally inherited the current quote's price.
    last_date = _timestamp(closed[-1]).date()
    offset = next((i for i,c in enumerate(closed) if _timestamp(c).date() == last_date), 0)
    session = closed[offset:]
    if len(session) < 12:
        return base
    higher = _completed(candles15 or [], 15, now)
    last_age = (now - _timestamp(session[-1])).total_seconds()
    is_recent = (last_age >= 0 and last_age <= tf_minutes*60 + 150)
    if demo:
        is_recent = True  # simulated bars for UI examples, always labelled

    ranges = [max(_f(b['high'])-_f(b['low']), _f(b['close'])*0.00015) for b in session]
    found = []
    for i in range(8, len(session)-2):
        anchor = session[i]
        history = session[max(0,i-9):i]
        avg = median(ranges[max(0,i-9):i])
        if len(history) < 6:
            continue
        recent_high = max(b['high'] for b in history)
        recent_low = min(b['low'] for b in history)
        for direction in ('bullish','bearish'):
            if direction == 'bullish' and not (anchor['close'] < anchor['open']):
                continue
            if direction == 'bearish' and not (anchor['close'] > anchor['open']):
                continue
            zone_low = _f(anchor['low']) if direction == 'bullish' else min(_f(anchor['open']),_f(anchor['close']))
            zone_high = max(_f(anchor['open']),_f(anchor['close'])) if direction == 'bullish' else _f(anchor['high'])
            if zone_high <= zone_low:
                continue
            for j in range(i+1,min(i+4,len(session))):
                candle = session[j]
                body = abs(_f(candle['close'])-_f(candle['open']))
                displacement = body/max(avg,1e-9)
                if direction == 'bullish':
                    bos = candle['close'] > recent_high and candle['close'] > candle['open']
                else:
                    bos = candle['close'] < recent_low and candle['close'] < candle['open']
                if not bos or displacement < 1.15:
                    continue
                volume_history = [_f(b.get('volume')) for b in session[max(0,i-9):i] if _f(b.get('volume'))>0]
                impulse_rvol = (_f(candle.get('volume')) / (sum(volume_history)/len(volume_history))) if len(volume_history)>=4 else None
                fvg = _fvg_near(session,i,j,direction)
                sweep = _sweep_before(session,i,direction)
                found.append(dict(anchor=i,break_index=j,direction=direction,
                                  zone_low=zone_low,zone_high=zone_high,
                                  displacement=displacement,volume_ratio=impulse_rvol,
                                  fvg=fvg,sweep=sweep,
                                  break_level=recent_high if direction=='bullish' else recent_low))
                break
    if not found:
        return base
    # Prefer the most recent structural break. Do not replace an invalidated
    # recent zone with a much older favorable zone.
    z = max(found,key=lambda o:(o['break_index'],o['anchor']))
    direction=z['direction'];lo=z['zone_low'];hi=z['zone_high']
    later=session[z['break_index']+1:]
    epsilon=max(.0001, median(ranges[-10:])*.05)
    retests=[];invalid=False
    for n,b in enumerate(later,z['break_index']+1):
        if (direction=='bullish' and _f(b['close']) < lo-epsilon) or (direction=='bearish' and _f(b['close']) > hi+epsilon):
            invalid=True
            break
        overlap=_f(b['low']) <= hi and _f(b['high']) >= lo
        if overlap:
            if direction=='bullish':
                rejection=_f(b['close'])>hi and _f(b['close'])>_f(b['open'])
            else:
                rejection=_f(b['close'])<lo and _f(b['close'])<_f(b['open'])
            retests.append((n,rejection))
    latest_retest = retests[-1] if retests else None
    fresh = not retests
    higher_aligned=_trend_confirmation(higher,direction)
    impulse_volume=z['volume_ratio']
    price=session[-1]['close']
    vwap=session[-1].get('vwap')
    vwap_aligned=(vwap is not None and
                  ((price > _f(vwap)) if direction=='bullish' else (price < _f(vwap))))
    vol_score=0
    if impulse_volume is not None:
        vol_score=round(15*min(1,max(0,impulse_volume/2.5)),1)
    elif rvol20 is not None:
        vol_score=round(12*min(1,max(0,_f(rvol20)/3)),1)
    displacement_score=round(12+13*min(1,z['displacement']/2.5),1)
    retest_recent=bool(latest_retest and latest_retest[1] and
                       len(session)-1-latest_retest[0] <= 1)
    parts={
        'bosDisplacement':min(25,displacement_score),
        'zoneFreshness':15 if fresh else (11 if len(retests)==1 else 4),
        'higherTimeframe':15 if higher_aligned else 0,
        'impulseVolume':vol_score,
        'fvg':10 if z['fvg'] else 0,
        'retestRejection':10 if retest_recent else 0,
        'liquiditySweep':6 if z['sweep'] else 0,
        'vwapAlignment':4 if vwap_aligned else 0,
    }
    points=min(100,max(0,round(sum(parts.values()))))
    expired = len(session)-1-z['break_index']>freshness_limit
    if invalid:
        status='INVALID';points=min(points,25)
        reason='A completed candle closed beyond the zone invalidation level'
    elif expired:
        status='WAIT'
        reason='Order block is too old; wait for a new structure break'
    elif not is_recent and not demo:
        status='WAIT'
        reason='Market candles are stale; wait for fresh completed bars'
    elif retest_recent and higher_aligned and points>=60:
        status='READY'
        reason='Recent confirmed zone retest + rejection, 15m trend and quality threshold agree'
    elif not retests:
        status='WAIT'
        reason='Fresh zone identified; waiting for a completed retest and rejection candle'
    else:
        status='WAIT'
        reason='Retest lacks a recent confirmation or sufficient higher-timeframe agreement'
    zone={'type':direction,'low':round(lo,2),'high':round(hi,2),
          'fromIndex':offset+z['anchor'],'breakIndex':offset+z['break_index'],
          'toIndex':len(closed)-1,'retests':len(retests),'fresh':fresh,
          'invalidated':invalid,'displacement':round(z['displacement'],2),
          'impulseVolumeRatio':round(impulse_volume,2) if impulse_volume is not None else None}
    fvg=z['fvg']
    if fvg:
        fvg={**fvg,'fromIndex':offset+fvg['fromIndex'], 'toIndex':offset+fvg['toIndex']}
    sweep=z['sweep']
    if sweep:
        sweep={**sweep,'index':offset+sweep['index']}
    return {**base,'obQualityScore':points,'obState':status,
            'obDirection':direction,'obReason':reason,'obBreakdown':parts,
            'obZone':zone,'obFvg':fvg,'obSweep':sweep,
            'obBos':{'index':offset+z['break_index'], 'price':round(z['break_level'],2),
                     'direction':direction},'obBasis':'SIMULATED' if demo else 'CANDLE_DERIVED'}
