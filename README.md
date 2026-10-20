> **Optimized for Render Free (512 MB RAM / 0.1 CPU).** This edition uses compact per-stock 20-day baselines, serial historical API calls, failure backoff, and a two-chart RAM cache. **Dashboard CSS and all layout/components are unchanged.** See [RENDER_FREE_GUIDE.md](RENDER_FREE_GUIDE.md) for deployment details and limitations.

# OrderFlow Studio — Premium 20-Day Kite Market Intelligence Edition

An original, screenshot-inspired intraday NSE stock scanner with the 198 unique user-supplied symbols, 14 sector groups, green/red market flow, top-10 bullish and bearish lists, diversified top-three picks, and clickable 5m / 15m / 30m / 1h stock charts with VWAP, price structure, and candidate order blocks.

**Now supports Zerodha Kite Connect live market data.** The dashboard is **read-only** — it never creates or sends buy/sell orders.

![Premium full-screen dashboard](preview-premium-dashboard.png)

![Enlarged multi-timeframe chart](preview-premium-chart.png)

[Watch the moving wires in a short demo preview](animated-wires-preview.mp4) · The preview uses synthetic demonstration market data, not Zerodha quotes.

## New: Full-screen, no-scroll trading-floor layout

The main dashboard has a **slim premium status header** with the OrderFlow logo, market LIVE/CLOSED/WAITING status, the NSE equity tick counter, historical-baseline loading progress, and DEMO or VIEWER indicator. Below it are the four columns from your reference screenshot, with no page scrolling:

**14 sector-strength rows → moving green/red momentum wires → top 10 bullish + top 10 bearish stock tiles → top 3 picks on both sides.**

- The entire map is fitted into one browser viewport (`100dvh`), including all 14 sectors, 20 ranked stock tiles (10 per direction), and six pick cards. No dashboard column or browser page scrollbar is needed on tested desktop screen sizes.
- Green and red strands are **genuinely animated**: SVG line highlights travel along sector paths, and small luminous dots move along selected connections. Wire ends are grouped into bullish and bearish zones. Motion is disabled if your browser requests reduced animation.
- Sector filtering, picking a stock, and chart access still work. Selecting a stock opens a **viewport-fitted popover** with the selectable 5m / 15m / 30m / 1h price chart, VWAP, candidate order blocks, OI/price classification, and overlaid reference levels; it does not cause page scrolling.
- Typography and contrast are deliberately **bolder and brighter** than before. At very narrow widths the four columns get compact, so a desktop monitor is recommended.
- The top market-status pill only displays **MARKET LIVE** when the Kite socket is connected and an NSE cash quote is fresh; the source indicator in the lower-left identifies `DEMO DATA` vs `KITE CONNECTED` / connection state. A connected WebSocket does not prove the market is presently open.
- The wires represent **modelled price momentum, not verified order-book depth, money transfers, or actual institutional orders**.

The fixed-screen presentation and wire animation are in `static/fullscreen.css` and `static/app.js`. The additional high-contrast premium treatment is in `static/premium.css`. The top-10 shortlist is enforced by `LEADER_LIMIT=10` in `server.py` and the top-three sector-diversified picks are selected from those ten per side. The original Kite backend, instrument mapping, streaming, and 5-minute data API continues to work, with higher-timeframe aggregation added.

**Chart-first detail view:** Clicking a symbol opens one enlarged multi-timeframe candlestick chart spanning the available dialog width. The former six-tile **Price structure & reference levels** sidebar has been removed. Price levels remain available as optional lines directly on the chart via **S/R levels**, alongside **VWAP** and **OB zones**. The popover does not scroll. Select **5m**, **15m**, **30m**, or **1h** in the upper-right of the chart: each button reloads the actual OHLCV candles and recalculates candidate OB zones, volatility-derived support/resistance, and crosshair values at the selected interval. The active timeframe follows you when you open another stock. The current partial bar updates as new Kite ticks arrive.

## 1. Install and run — Kite Connect live mode

Use **Python 3.10+** and the **paid Kite Connect market-data plan**. (Zerodha's free Personal plan does **not** include live market-data WebSockets or historical 5-minute candles.)

```bash
# In the folder containing server.py:
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Make your **private** settings file by copying `.env.example` to `.env`. Edit it in a text editor:

```dotenv
MARKET_MODE=kite
KITE_API_KEY=YOUR_EXISTING_KITE_API_KEY
KITE_ACCESS_TOKEN=YOUR_VALID_ACCESS_TOKEN_FOR_TODAY
HOST=127.0.0.1
PORT=8000
```

Start the server:

```bash
python server.py
```

Open **http://127.0.0.1:8000**. The data-source indicator at the bottom-left of the wires panel shows the Kite connection state after the WebSocket handshake. Hover over it to see the latest quote timestamp or the feed status/error. `GET /api/health` exposes mapped futures counts and missing stock mappings. Quotes may be **stale outside NSE trading hours**; the connection indicator does **not** imply that the exchange is currently open.

**Important security**: keep the API key and access token in your `.env` file on your own computer. Never upload real secrets to ChatGPT, paste them into browser source code, or commit `.env` to Git. `.env` is in `.gitignore` and **not included** in this ZIP. Don't expose port 8000 to the public internet; the local UI has no user login. Use `127.0.0.1` (default).

Zerodha access tokens are **short-lived**, normally expiring by the next trading day. When it expires, generate a new access token through Zerodha's authorized login procedure, update `.env`, then restart this program. Having an API key alone does not enable the live feed.

## 2. How live market data works

1. On launch, the backend downloads Zerodha's **current NSE and NFO instrument masters** and resolves symbol names to numeric instrument tokens. Symbols that cannot be matched exactly are omitted; the app will **not silently substitute** another instrument.
2. It initializes stock prices using a single set of NSE cash `quote` requests so cards display even after market close.
3. A single KiteTicker WebSocket subscribes to **cash-equity stocks in QUOTE mode** (LTP, previous close, volume, OHLC, available average price) and **nearest-expiry, nonexpired NFO stock futures in FULL mode** (OI when available).
4. NSE cash last traded prices determine bullish/bearish direction. In Kite mode when at least 14 historical sessions are loaded, score = 22 × capped |day change %| / ADR20 + 9 × capped matched-time RVOL + 7 on 20-day breakout + 5 if price is aligned with VWAP + up to 2.88 from available positive futures OI %. While histories warm, it uses the old live-direction fallback score and labels RVOL as pending. It is **not a reverse-engineering of order-block.com's private proprietary ranking algorithm**.
5. The dashboard publishes updates to the browser over **Server-Sent Events every ~2 seconds**, not once per raw WebSocket tick.
6. At startup, a background worker loads up to **20 completed trading sessions** per mapped stock, downloading prior 5-minute + daily Kite candles, computing historical baselines, and caching compact derived indicators (not the full candles) on disk. Full historical chart candles load only when a stock is selected. The header shows baseline progress. Clicking a symbol requests a separate bounded chart backfill, overlays live quote updates and refreshes its chart while open. Only two recently opened charts keep longer histories in RAM. The entire first warm-up across ~198 symbols can take several minutes.
7. VWAP is constructed from candlestick (typical price × candle volume), with the running calculation **reset each trading session**; Kite's session average traded price is preferred in the quick summary when supplied.
8. Candidate order blocks are calculated from candle-price action using a preceding opposing candle, impulse, and local break of structure. **These rectangles are not actual order-book liquidity or verified institutional orders.** Support/resistance bands are simple volatility-derived reference levels, not entry signals.

### NSE cash vs NFO futures open interest

- **Cash equities do not have a meaningful derivatives OI stream.** Their cash price is displayed separately from the nearest listed *stock futures contract's* OI.
- OI is shown **only** when a matching, unexpired NFO FUT instrument exists and the Kite feed returns that value; otherwise the UI displays `N/A` / `OI UNAVAILABLE`.
- The displayed `FUTURES OI Δ` is relative to the **first observed OI since the app connected**, not last day's OI. This distinction matters for "long build-up" and "short covering" interpretations; it should not be mistaken for exchange-reported previous-day OI change.
- `RVOL` compares **today’s cumulative NSE cash volume to the average cumulative volume at the same five-minute market-time bucket across prior completed sessions** (minimum 10 valid intraday history sessions). The UI displays N/A / 20D pending until the reference is loaded; it never fabricates missing RVOL.
- Stocks in your configured list are **not all guaranteed to be active F&O stocks today**. Their live eligibility depends on what the NSE/NFO instrument masters actually contain. `NIFTY_50` is a **cross-sector watchlist**, not an additional sector, so it is not double counted.

### Selectable candle timeframes (new)

The chart header has four bold buttons: **5m | 15m | 30m | 1h**. They change the candle aggregation itself—not merely its title or zoom. The server retains 5-minute bars from Kite and aggregates OHLCV without crossing session boundaries; a 1-hour candle is anchored at the NSE 09:15 IST open (09:15–10:15, 10:15–11:15, etc). Bar VWAP is inherited from the last constituent 5-minute bar to keep the same session VWAP reference; price structure and order blocks are computed on the selected candles. Session dates appear on the x-axis when history spans multiple trading days.

Use `GET /api/symbol/PGEL?timeframe=5m`, `?timeframe=15m`, `?timeframe=30m` or `?timeframe=1h`. Unsupported values return HTTP 400. The Kite connection and access token remain server-side. **Historical availability and OB zone counts depend on the data actually received** (a very short history may yield no candidate order blocks). Current/incomplete candles are provisional.

### 5-minute volume and live-feed limitations

The live Kite QUOTE feed provides **cumulative traded volume**, not individual exchange transaction volume. This adapter calculates positive deltas between consecutive quotes (resetting at each new trading session). When you start halfway through a 5-minute period, the initial tick cannot reconstruct earlier traded volume in that bar. Historical 5-minute candles from Kite backfill when you open the stock; the newest candle remains provisional until it closes. A single WebSocket is used for all configured cash symbols plus any eligible futures, remaining within Kite's documented subscription capacity.

The history worker serializes calls (~1.8 historical calls/second by default), yields between symbols, prioritizes leading movers, limits retries, and saves compact per-symbol baseline caches; no feed can guarantee zero missed ticks over lost network connections. This scanner is for research/educational monitoring and requires thorough backtesting and reconciliation before taking trading decisions.

## 2A. Previous-20-trading-session intelligence and daily reset

- **ADR20:** mean of daily high-low range as a percentage of that session's closing price, over the previous 20 *completed* sessions.
- **ATR14:** average of the last 14 daily true ranges using prior closes; useful as a volatility baseline.
- **20-day high/low:** highest high and lowest low from those prior sessions, excluding today's incomplete candle.
- **Time-of-day RVOL:** current live cumulative equity volume divided by the historical average cumulative volume at the *same* NSE 5-minute time-of-session slot. Requires at least 10 sufficiently complete historical intraday sessions (75 bars on a standard session, at least 60 to qualify).
- **History readiness:** per stock READY only with all 20 valid daily and 20 intraday sessions, otherwise PARTIAL, REFRESHING, or LOADING. A partial baseline may still support ADR/ATR and rankings where sufficient sessions exist; missing RVOL stays null/N/A.
- **Daily rollover (IST):** on the first genuine exchange cash tick with a new session date, the tick counter, cumulative-volume reference, and OI baseline are reset. Intraday candle VWAP starts a new session each day. The previous 20-day historical calculations are NOT discarded: the background worker refreshes them to include the newly completed prior session on the next eligible weekday. Exchange holidays/special sessions are not automatically resolved; market status only says LIVE with a *fresh actual tick*, not just because the calendar says it should be open.
- **Storage on Render:** by default history caches are in `.cache/history`, which may be lost after a Render deploy/restart. On a paid Render service with a persistent disk mounted at `/var/data`, add environment variable `HISTORY_CACHE_DIR=/var/data/orderflow-history` (mount path must match). Cache stores public historical OHLCV only, NEVER API secrets. On an ephemeral instance, the scanner will re-download after restarts (hundreds of history requests and several minutes warm-up).
- **Kite credentials:** the access token is still renewed via your authorized Zerodha login; this project does not automatically log in or create a new access token. The viewer badge is only a display label, NOT login/access control. Protect the public Render URL before sharing market data.

## 3. Demo mode — no broker credentials

To try the UI offline, change `.env` to `MARKET_MODE=demo` (or remove `.env`, which defaults to demo), restart and visit the same URL. **Every demo number is synthetic** and labelled as a demo. Demo data is never silently substituted for Kite data if authentication fails; Kite mode instead displays a connection error.

## 4. Legacy authenticated external tick-ingestion mode (optional)

The original project is preserved for custom licensed feeds. This is separate from the **direct Kite integration** above:

```dotenv
MARKET_MODE=live
MARKET_FEED_TOKEN=your-private-long-string
```

Then `POST /api/ingest` with header `X-Feed-Token`. This endpoint is **disabled in Kite mode** to prevent accidental mixing of external and Zerodha data. The included `examples/ingest_jsonl.py` demonstrates that legacy protocol.

## 5. Endpoints and testing

| Endpoint | Description |
|---|---|
| `GET /` | Interactive dashboard |
| `GET /api/state` | Ranked stock list, sector matrix, feed health and breadth |
| `GET /api/symbol/PGEL?timeframe=15m` | Selected stock detail with 5m, 15m, 30m or 1h candles and recomputed order blocks/levels (default 5m) |
| `GET /api/universe` | All exact configured symbols, sectors and NIFTY_50 watchlist |
| `GET /api/health` | `mode`, `feedStatus`, `lastQuoteAt`, count of missing mappings |
| `GET /api/events` | SSE browser event stream |
| `POST /api/ingest` | Legacy custom-feed mode only (disabled for Kite / demo) |

To run all offline integration tests:

```bash
python -m unittest -v
```

`test_kite_feed.py` uses a stubbed Kite client and ticker to verify instrument mapping, subscription modes, 5-minute history, OI, daily volume changes, and reconnection handling without requesting real credentials or connecting to the internet. The real Zerodha connection **cannot be verified without your valid live tokens and network access**.

## 6. Project layout

```text
orderflow-studio/
├── .env.example         # template only — no keys or tokens
├── .gitignore           # prevents accidental .env commit
├── README.md
├── requirements.txt     # FastAPI, uvicorn, official kiteconnect, python-dotenv
├── sectors.py           # exact user-provided SECTOR_DEFINITIONS & ALL_SYMBOLS
├── server.py            # ranking engine, OB/VWAP/5m OHLC, SSE, FastAPI
├── history_intelligence.py # pure 20-session historical metrics + compressed OHLCV cache
├── kite_feed.py         # real Zerodha instruments/quote/WebSocket/history adapter
├── test_scanner.py      # scanner computation tests
├── test_kite_feed.py    # Kite integration tests with stubbed SDK
├── examples/ingest_jsonl.py
└── static/
    ├── index.html
    ├── app.js            # flow diagram, top 10→3, stock detail, live chart refresh
    ├── styles.css        # core theme and detail-chart styling
    ├── fullscreen.css    # no-scroll 100dvh map; animated wires
    ├── premium.css       # high-contrast two-column top-10 tiles + larger chart
    └── favicon.svg
```

### Troubleshooting

- **KITE · ERROR**: ensure the token is valid today and your Kite Connect subscription includes paid market data. Restart after updating `.env`. Watch the server console for an exception class (credentials are never included in browser error messages).
- **Missing symbols**: some supplied names may be retired, renamed, or absent from Zerodha's current instrument master. Check `GET /api/health` for count and `GET /api/state` for the list; avoid manually guessing token IDs.
- **Prices not moving**: NSE may be closed or the feed may be reconnecting. Check `lastQuoteAt`, rather than equating a WebSocket connection with an open market.
- **Stock chart empty**: Kite historical-data entitlement / connection may be unavailable; the viewer shows only candles actually received by the feed. Opening a symbol triggers one on-demand history request, with a 60-second cache.
- **No OI for PGEL**: this is expected unless that stock has an eligible, quoted FUT contract; no fake OI is added.
- **Rate limits**: the server uses one market-data WebSocket, batched quote requests, and serialized, cached historical requests.

## Official Zerodha references

- [Official Python client](https://github.com/zerodha/pykiteconnect)
- [WebSocket streaming](https://kite.trade/docs/connect/v3/websocket/)
- [Historical candles](https://kite.trade/docs/connect/v3/historical/)
- [Instrument master and quote API](https://kite.trade/docs/connect/v3/market-quotes/)
- [Authentication / expiration](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs)

**Read-only, educational project. No order placement. Not investment advice.** The dashboard is an original design inspired by the provided screenshots; it does not claim to reproduce the reference website's proprietary backend or ranking logic.

## Deploy this version to Render

1. Upload the source files (not `.env` and not `.cache/`) to a **private GitHub repository**. Optional `render.yaml` supports Render Blueprints; alternatively create a Web Service manually.
2. Build: `pip install -r requirements.txt`; start: `uvicorn server:app --host 0.0.0.0 --port $PORT`. Use **one worker** because scanner state and WebSocket are in-memory.
3. Add `MARKET_MODE=kite`, and your existing `KITE_API_KEY` and **current-day** `KITE_ACCESS_TOKEN` as private Render environment variables.
4. For an always-on paid service, optionally attach a Render persistent disk at `/var/data` and set `HISTORY_CACHE_DIR=/var/data/orderflow-history` (without this, each redeploy may seed all 198 histories again).
5. `/api/health` includes `marketStatus`, `tickCount`, `historyLoaded`, `historyTarget`, and `historyLoading`. A connected socket is **not** proof of fresh live ticks. Market-status holidays are detected through actual tick freshness, not an official NSE calendar.
6. Kite access tokens still expire and require secure renewal via Zerodha's login flow. **The UI is not login-protected**; never make this service public to other users without adding authentication and verifying market-data redistribution rights.

During historical warm-up, baseline progress rises through the stock universe. It can take several minutes because each mapped stock requires up to two Kite history requests. An unfetched or partially available history displays N/A/PENDING for time-of-day RVOL instead of fabricated data.

## Mobile & tablet responsive edition

The front end now includes `static/mobile.css`, loaded after the existing desktop style sheets.
It is automatically active at browser widths **960 px and below**, and needs no configuration or separate mobile application.

- **Desktop (>960 px):** original fixed, single-screen desk layout with sectors, animated wires, 10 bullish / 10 bearish leaders, and 3 picks each side.
- **Tablets (601–960 px):** swipeable sector filter, compact animated flow map, side-by-side bullish/bearish leader groups, and a two-column picks area. The page may **scroll vertically** to keep text and touch targets readable.
- **Phones (up to 600 px):** stacked, full-width bullish and bearish lists, six picks in two groups of three, and a mobile-first header. Sector filters scroll **horizontally inside their own row**, not the page. All stocks remain selectable.
- **Detail chart:** full-screen on tablet/phone with 5m / 15m / 30m / 1h selector, VWAP, support/resistance and candidate order blocks. On phones, the most recent 50 candles are shown to keep bars readable, but underlying historical indicators remain calculated using their normal data horizon.
- **Rotation:** chart and flow map resize automatically when the browser width changes.
- **Render Free:** backend settings and Zerodha integration are unchanged. No additional environment variables are required; redeploy the latest GitHub commit.

Mobile screenshots bundled as previews are **demo-data captures**, not live NSE prices. On Render, a working `MARKET_MODE=kite` connection is still necessary for live prices. Small displays deliberately allow **vertical page scrolling**; squeezing all 20 leaders onto one unscrollable phone screen would make the interface unusable.
