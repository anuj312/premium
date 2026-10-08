import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market import DemoProvider, MarketEngine, build_up, pct_change
from sectors import ALL_SYMBOLS, SECTOR_ONLY, SYMBOL_SECTOR, NIFTY_50_SET


def test_universe():
    assert len(ALL_SYMBOLS) == len(set(ALL_SYMBOLS)) == 198
    assert len(SECTOR_ONLY) == 14
    assert 'BHARTIARTL' in NIFTY_50_SET and 'BHARTIARTL' not in SYMBOL_SECTOR


def test_buildup():
    assert build_up(1,2) == 'LONG BUILD-UP'
    assert build_up(-1,2) == 'SHORT BUILD-UP'
    assert build_up(1,-2) == 'SHORT COVERING'
    assert build_up(-1,-2) == 'LONG UNWINDING'
    assert build_up(None,2) == 'WAITING FOR OI'
    assert build_up(0,2) == 'NEUTRAL'
    assert pct_change(105,100) == 5.0
    assert pct_change(0,0) is None


def test_demo_engine():
    engine = MarketEngine(DemoProvider(), 'demo')
    first = engine.update()
    assert first['status'] == 'ok'
    assert first['mode'] == 'demo'
    assert first['summary']['total'] == 198
    assert len(first['sectors']) == 14
    assert all(s['oi_change_pct'] == 0 for s in first['stocks'])
    second = engine.update()
    assert second['summary']['total'] == 198
    assert any(s['oi_change_pct'] != 0 for s in second['stocks'])
    assert second['meta']['oi_basis'].startswith('% change since')


class FakeKite:
    def instruments(self, exchange):
        if exchange == 'NSE':
            return [{'tradingsymbol':'INFY','instrument_type':'EQ'},
                    {'tradingsymbol':'RELIANCE','instrument_type':'EQ'},
                    {'tradingsymbol':'INFY','instrument_type':'INDEX'}]
        return [{'instrument_type':'FUT','name':'INFY', 'tradingsymbol':'INFY26OCTFUT','expiry':'2026-10-29'},
                {'instrument_type':'FUT','name':'INFY', 'tradingsymbol':'INFY26NOVFUT','expiry':'2026-11-26'},
                {'instrument_type':'FUT','name':'RELIANCE', 'tradingsymbol':'REL26NOVFUT','expiry':'2026-11-26'},
                {'instrument_type':'CE','name':'INFY','tradingsymbol':'INFY26OCT1000CE','expiry':'2026-10-29'}]


def test_expiry_and_missing_symbols(monkeypatch):
    from market import LiveKiteProvider
    import types
    class Stub:
        def __init__(self,api_key): pass
        def set_access_token(self,token): pass
        instruments=FakeKite.instruments
    monkeypatch.setitem(sys.modules,'kiteconnect',types.SimpleNamespace(KiteConnect=Stub))
    provider=LiveKiteProvider('fake','fake')
    assert provider.futures['INFY']['tradingsymbol']=='INFY26OCTFUT'
    assert 'TCS' in provider.missing_nse
    assert 'TCS' not in provider.futures
