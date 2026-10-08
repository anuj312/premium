"""One-time daily Kite login helper. Run locally; never put API_SECRET in frontend."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from dotenv import dotenv_values, set_key

ENV_PATH = Path(__file__).resolve().parent / '.env'
values = dotenv_values(ENV_PATH)
key = values.get('KITE_API_KEY') or input('Kite API key: ').strip()
secret = values.get('KITE_API_SECRET') or input('Kite API secret: ').strip()
if not key or not secret:
    raise SystemExit('Kite API key and API secret are required.')
try:
    from kiteconnect import KiteConnect
except ImportError:
    raise SystemExit('Install requirements: pip install -r requirements.txt')
kite = KiteConnect(api_key=key)
print('\nOpen this login URL in your browser:\n' + kite.login_url())
value = input('\nPaste request_token OR the full redirect URL: ').strip()
request_token = parse_qs(urlparse(value).query).get('request_token', [value])[0]
if not request_token:
    raise SystemExit('No request_token found.')
session = kite.generate_session(request_token, api_secret=secret)
for k, v in [('MODE','live'), ('KITE_API_KEY',key), ('KITE_API_SECRET',secret),
             ('KITE_ACCESS_TOKEN',session['access_token'])]:
    set_key(str(ENV_PATH), k, v)
try:
    ENV_PATH.chmod(0o600)
except OSError:
    pass
print('Access token saved in .env. Restart your dashboard server.')
