"""Bridge your licensed market-data source to OrderFlow Studio.

Read one JSON object per line from stdin and forward in short batches to /api/ingest.
This program does NOT fetch market data or provide broker credentials.

Required environment:
  MARKET_FEED_TOKEN: bearer-like secret matching server's token
Optional:
  ORDERFLOW_URL: http://127.0.0.1:8000

Example:
  echo '{"symbol":"PGEL","price":527.65,"prev_close":487.70,"volume_increment":1200,"oi":2400000,"oi_base":2100000,"rvol":2.2}' \
    | python examples/ingest_jsonl.py

Adapt your broker's legally acquired WebSocket on_tick callback to print JSONL into this script.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = os.getenv('ORDERFLOW_URL', 'http://127.0.0.1:8000').rstrip('/') + '/api/ingest'
TOKEN = os.getenv('MARKET_FEED_TOKEN', '')
if not TOKEN:
    raise SystemExit('MARKET_FEED_TOKEN must be set')


def send(batch):
    data = json.dumps({'quotes': batch}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={
        'Content-Type': 'application/json', 'X-Feed-Token': TOKEN}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            res = json.load(r)
            print(f"accepted={res['accepted']} rejected={len(res['errors'])}", file=sys.stderr)
            for err in res['errors']:
                print('REJECTED:', err, file=sys.stderr)
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code}: {e.read().decode()}', file=sys.stderr)
        raise SystemExit(1)


batch = []
for raw in sys.stdin:
    if not raw.strip():
        continue
    try:
        tick = json.loads(raw)
        if not isinstance(tick, dict) or 'symbol' not in tick or 'price' not in tick:
            raise ValueError('Expected a JSON object with symbol and price')
    except (ValueError, json.JSONDecodeError) as exc:
        print('Skipping invalid tick:', exc, file=sys.stderr)
        continue
    batch.append(tick)
    if len(batch) >= 50:
        send(batch)
        batch = []
if batch:
    send(batch)
