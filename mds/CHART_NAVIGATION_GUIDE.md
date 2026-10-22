# OrderFlow Studio — Interactive Chart Navigation

All four existing timeframes (**5m / 15m / 30m / 1h**) can be explored with the same controls.

- **+ / −**: enlarge or reduce the visible candle scale around the center of the chart.
- **Mouse wheel**: zoom around the candle under your cursor.
- **Mouse drag**: pan backward or forward through candles.
- **Touch drag**: scroll through candle history with one finger while touching the chart.
- **Pinch in/out**: zoom around the two-finger midpoint.
- **Fit all** (four-corner icon): show every candle already loaded in this timeframe.
- **LIVE ↗**: jump to the newest loaded candles.
- **Keyboard:** focus the chart with Tab or a click; use +, −, ←, →, Home and End.
- **Double-click the chart**: fit all loaded candles.

The tooltip appears on mouse hover, showing OHLC, traded volume and VWAP for the candle under the cursor. On a mobile touch screen, use the navigation controls and zoom gestures; mouse hover is not required.

## Refresh behavior

The viewport tracks candle indices. During a 15-second chart refresh, an older panned view remains on its historical candles. If the viewport is at the newest candle, it follows any newly appended candle. A timeframe or stock change resets to that selection's default view. The 20-day historical scanner, order-block intelligence, ranking and Render configuration are unchanged.

## Data and trading safety

Zoom does not generate additional historical candles, trade delta, or order-book depth. It only changes how **already fetched** OHLCV and computed overlays are displayed. The live quote feed still requires your own valid Kite access token. The order-block state and quality score are research heuristics, not guaranteed entries.
