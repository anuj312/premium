# TradingView 5-minute chart shortcuts

This release adds **TV ↗** to all **Top 6 bullish**, **Top 6 bearish**, **Top 3 bullish**, and **Top 3 bearish** cards. It also adds **TradingView ↗** immediately beside **AUTO** in the expanded stock chart toolbar.

## How it works

- Press a stock card (outside the TV icon) to open OrderFlow Studio's existing chart.
- Press **TV ↗** to open that stock's TradingView chart in a **new browser tab**.
- Press **TradingView ↗** in the expanded chart to open the selected stock externally.
- External destination: `https://www.tradingview.com/chart/?symbol=NSE%3APGEL&interval=5` for PGEL, and equivalently encoded `NSE:<symbol>` for any other supported stock.
- `interval=5` requests **5-minute candles** regardless of the timeframe selected in OrderFlow Studio.
- Browser popup blockers generally allow this because a regular anchor with `target="_blank"` is used directly from a real user click.
- TradingView may apply account-specific saved chart layouts, saved timeframe settings, or symbol availability and therefore can override the requested 5-minute interval. Confirm **5m** in TradingView if it doesn't open at that timeframe.
- The shortcut opens the **NSE cash equity** chart; it does not attempt to map F&O futures or option contracts.

## Privacy, resources and deployment

- No TradingView API key, additional Python dependency or Kite API request is required.
- No sensitive Kite credentials are added to external URLs.
- The new buttons are responsive for mobile/tablet and support keyboard focus.
- HTML and critical assets use a content-based fingerprint and `no-store` to prevent old JavaScript from persisting after Render deploys.
- The scanner's Kite Connect feed, ranking logic, animated wires, auto-follow, chart overlays, and Render Free settings remain unchanged.

## Validation

- Automated Python/API and Node renderer tests check the NSE 5-minute deep links and verify that stock buttons and external anchors are separate HTML elements.
- A Chromium offline visual run with the app's demo snapshot verified desktop (1440x900) and mobile (390x844) layouts, 12 Top 6 TV links, six Top 3 TV links, chart toolbar links, and no sideways page overflow.
- TradingView's remote website itself cannot be tested or controlled in this environment.
