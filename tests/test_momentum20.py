from datetime import date,timedelta,datetime
from momentum20 import daily_baseline,momentum_multiple,demo_baselines
from market import MarketEngine,DemoProvider


def test_prior_twenty_completed_days_excludes_today():
    start=date(2026,7,1)
    rows=[{'date':start+timedelta(days=i),'close':100*(1.01**i)} for i in range(21)]
    rows.append({'date':start+timedelta(days=21),'close':9999})
    result=daily_baseline(rows,start+timedelta(days=21))
    assert round(result,2)==1.0
    assert momentum_multiple(3,result)==3.0
    assert momentum_multiple(-2,result)==-2.0


def test_insufficient_data_and_zero_baseline():
    assert daily_baseline([{'date':date(2026,7,1),'close':100}],date(2026,8,1)) is None
    rows=[{'date':date(2026,7,1)+timedelta(days=i),'close':100} for i in range(21)]
    assert daily_baseline(rows,date(2026,8,1)) is None
    assert momentum_multiple(3,None) is None


def test_simulation_and_live_snapshot_fields():
    demo=DemoProvider(); engine=MarketEngine(demo,'demo')
    engine.reset_momentum20()
    fake=demo_baselines(demo.nse_symbols)
    for sym, avg in fake.items(): engine.set_momentum20_baseline(sym,avg)
    engine.set_momentum20_progress(status='ready',processed=len(fake),available=len(fake),total=len(fake))
    s=engine.update()
    assert all(x['momentum_20d_x'] is not None for x in s['stocks'])
    assert len(s['sectors'])==14
    assert s['meta']['momentum20']['status']=='ready'
    assert demo_baselines({'INFY'})==demo_baselines({'INFY'})


def test_demo_20d_loader_does_not_block_web_server(monkeypatch):
    import main
    import time
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'MODE','demo')
    with TestClient(main.app) as client:
        assert client.get('/healthz').status_code == 200
        for _ in range(25):
            data=client.get('/api/state').json()
            if data.get('meta',{}).get('momentum20',{}).get('status')=='ready':
                break
            time.sleep(.1)
        assert data['meta']['momentum20']['available']==198
        assert data['status']=='ok'
        assert client.get('/healthz').status_code == 200


def test_v11_momentum_score_alias_and_missing_baselines():
    demo = DemoProvider()
    engine = MarketEngine(demo, 'demo')
    symbols = sorted(demo.nse_symbols)
    symbol = symbols[0]
    initial = engine.update()
    before = next(s for s in initial['stocks'] if s['symbol'] == symbol)
    assert before['momentum_score'] is None
    assert before['momentum_20d_x'] is None

    engine.set_momentum20_baseline(symbol, 1.5)
    updated = engine.update()
    after = next(s for s in updated['stocks'] if s['symbol'] == symbol)
    assert after['momentum_score'] == after['momentum_20d_x']
    assert after['momentum_score'] == momentum_multiple(after['change_pct'], 1.5)
    other = next(s for s in updated['stocks'] if s['symbol'] != symbol)
    assert other['momentum_score'] is None
    assert '20 prior completed sessions' in updated['meta']['momentum_score_basis']
