"""No network access: simulated KiteTicker callback tests."""
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market import KiteTickerProvider, MarketEngine


class DummyTicker:
    MODE_FULL = 'full'
    instance = None

    def __init__(self, api_key, access_token, reconnect=True):
        self.credentials = (api_key, access_token)
        self.reconnect = reconnect
        self.subscribed = []
        self.modes = []
        self.closed = False
        DummyTicker.instance = self

    def connect(self, threaded=False):
        self.threaded = threaded
        self.on_connect(self, {'type': 'success'})

    def subscribe(self, tokens):
        self.subscribed = tokens

    def set_mode(self, mode, tokens):
        self.modes.append((mode, tokens))

    def close(self):
        self.closed = True


class DummyKite:
    def __init__(self, api_key):
        self.api_key = api_key
        self.quote_calls = 0

    def set_access_token(self, token):
        self.token = token

    def instruments(self, exchange):
        if exchange == 'NSE':
            return [
                {'tradingsymbol': 'INFY', 'instrument_type': 'EQ', 'instrument_token': 1001},
                {'tradingsymbol': 'RELIANCE', 'instrument_type': 'EQ', 'instrument_token': 1002},
            ]
        return [
            {'name': 'INFY', 'instrument_type': 'FUT', 'tradingsymbol': 'INFY26OCTFUT', 'expiry': '2026-10-29', 'instrument_token': 2001},
            {'name': 'INFY', 'instrument_type': 'FUT', 'tradingsymbol': 'INFY26NOVFUT', 'expiry': '2026-11-26', 'instrument_token': 2002},
        ]

    def quote(self, *keys):
        self.quote_calls += 1
        all_quotes = {
            'NSE:INFY': {'last_price': 1500, 'ohlc': {'close': 1490, 'high': 1510, 'low': 1480}, 'volume': 100, 'average_price': 1497},
            'NSE:RELIANCE': {'last_price': 1400, 'ohlc': {'close': 1390}, 'volume': 300, 'average_price': 1399},
            'NFO:INFY26OCTFUT': {'last_price': 1510, 'oi': 10000},
        }
        return {key: all_quotes[key] for key in keys if key in all_quotes}


def test_tick_provider_connect_and_cache(monkeypatch):
    monkeypatch.setitem(sys.modules, 'kiteconnect', types.SimpleNamespace(KiteConnect=DummyKite, KiteTicker=DummyTicker))
    provider = KiteTickerProvider('test-key', 'test-token')
    assert provider.futures['INFY']['tradingsymbol'] == 'INFY26OCTFUT'
    provider.start()
    ticker = DummyTicker.instance
    assert ticker.threaded is True
    assert ticker.reconnect is True
    assert set(ticker.subscribed) == {1001, 1002, 2001}
    assert len(ticker.modes) == 1
    assert ticker.modes[0][0] == 'full'
    assert provider.kite.quote_calls == 1
    assert provider.feed_info()['feed_state'] == 'waiting_for_ticks'

    ticker.on_ticks(ticker, [
        {'instrument_token': 1001, 'last_price': 1515, 'volume_traded': 12500,
         'average_traded_price': 1501, 'total_buy_quantity': 700,
         'total_sell_quantity': 500, 'last_traded_quantity': 15,
         'ohlc': {'close': 1490, 'high': 1515, 'low': 1480}},
        {'instrument_token': 2001, 'last_price': 1520, 'oi': 10500},
    ])
    assert provider.kite.quote_calls == 1  # No REST calls after startup.
    quotes = provider.quotes()
    assert quotes['NSE:INFY']['last_price'] == 1515
    assert quotes['NSE:INFY']['volume'] == 12500
    assert quotes['NSE:INFY']['buy_quantity'] == 700
    assert quotes['NSE:INFY']['sell_quantity'] == 500
    assert quotes['NSE:INFY']['last_quantity'] == 15
    assert quotes['NFO:INFY26OCTFUT']['oi'] == 10500
    assert quotes['NSE:RELIANCE']['last_price'] == 1400  # seed retained for inactive symbols
    assert provider.feed_info()['feed_state'] == 'streaming'
    assert provider.feed_info()['received_ticks'] == 2

    engine = MarketEngine(provider, 'live')
    state = engine.update()
    assert state['status'] == 'ok'
    assert state['meta']['feed'] == 'ticker'
    assert state['meta']['subscribed_tokens'] == 3
    assert state['summary']['total'] == 2
    assert provider.kite.quote_calls == 1
    provider.stop()
    assert ticker.closed


def test_tick_mode_normalizes_missing_optional_fields():
    assert KiteTickerProvider.normalize_tick({'last_price': 42}, {'volume': 100, 'oi': 200}) == {
        'last_price': 42, 'volume': 100, 'oi': 200
    }
