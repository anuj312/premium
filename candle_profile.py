"""Compact *estimated* price/volume distributions from OHLCV candles.

Not an exchange volume-at-price feed, aggressor delta, market depth or resting
orders. Each candle's actual traded volume is spread uniformly across its
observed high-low range; a low-cost illustration suited to the Render Free UI.
"""
from __future__ import annotations

from math import isfinite


def candle_volume_profile(candles: list[dict], bins: int = 17, max_candles: int = 42) -> dict:
    bins = max(5, min(40, int(bins)))
    # Ignore corrupt or zero-volume candles, which should never make up volume.
    valid = []
    for c in candles[-max_candles:]:
        try:
            lo, hi, vol = float(c['low']), float(c['high']), float(c['volume'])
            if all(isfinite(v) for v in (lo, hi, vol)) and lo > 0 and hi >= lo and vol > 0:
                valid.append((lo, hi, vol))
        except (TypeError, ValueError, KeyError):
            continue
    if not valid:
        return {'bars': [], 'poc': None, 'source': 'ESTIMATED_5M_OHLCV', 'observations': 0}
    floor = min(lo for lo, _, _ in valid)
    ceiling = max(hi for _, hi, _ in valid)
    step = max((ceiling - floor) / bins, floor * .00001, .000001)
    volume_bins = [0.0] * bins
    for low, high, volume in valid:
        # Uniform within-candle price occupancy is an explicit approximation.
        if high == low:
            target = max(0, min(bins-1, int((low-floor)/step)))
            volume_bins[target] += volume
            continue
        span = high-low
        for idx in range(bins):
            a, b = floor+idx*step, floor+(idx+1)*step
            overlap = max(0.0, min(b, high)-max(a, low))
            if overlap:
                volume_bins[idx] += volume*(overlap/span)
    top = max(volume_bins)
    poc = max(range(bins), key=lambda i: volume_bins[i])
    return {'bars': [round(v/top, 4) if top else 0.0 for v in volume_bins],
            'poc': poc,
            'low': round(floor, 2), 'high': round(ceiling, 2),
            'pocPrice': round(floor+(poc+.5)*step, 2),
            'source': 'ESTIMATED_5M_OHLCV', 'observations': len(valid)}
