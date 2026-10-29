# Chart controls right alignment — deployment fix

The TF / AUTO / TradingView / zoom buttons are pinned to the far-right edge in the stock-detail window.

On wide screens, the OB explanation stays at the far-left end of the toolbar row.
At tablet/phone widths the control group remains right-aligned and responsive.

The static `detail-chart-focus.css` content is included in `UI_ASSET_VERSION` and served no-store to avoid older cached CSS from overriding the deployed positioning.

Stock selection, metrics, chart behavior, and Top 3 ranking are unchanged.
