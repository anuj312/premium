"""Point-in-time market features. No network calls or account credentials."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
OPEN, CLOSE = time(9, 15), time(15, 30)
COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
        return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)
    except (ValueError, TypeError):
        return None


def market_open(now):
    return now.weekday() < 5 and OPEN <= now.time() < CLOSE


def normalize(candles):
    frame = candles.copy() if isinstance(candles, pd.DataFrame) else pd.DataFrame(candles)
    if frame.empty or not set(COLUMNS).issubset(frame.columns):
        return pd.DataFrame(columns=COLUMNS)
    frame = frame[COLUMNS].copy()
    frame["date"] = frame["date"].map(timestamp)
    for name in COLUMNS[1:]:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=COLUMNS)
    frame = frame[(frame["low"] > 0) & (frame["high"] >= frame["low"]) & (frame["volume"] >= 0)]
    return frame.drop_duplicates("date", keep="last").sort_values("date", kind="stable").reset_index(drop=True)


def complete_bars(frame, now, daily=False):
    if frame.empty:
        return frame.copy()
    if daily:
        return frame[frame["date"].dt.date < now.date()].copy()
    return frame[frame["date"] + pd.Timedelta(minutes=5) <= now].copy()


def indicators(frame):
    if len(frame) < 50:
        return {"rsi": None, "adx": None, "ema_price": None, "ema_fast": None}
    close, high, low = frame["close"], frame["high"], frame["low"]
    delta = close.diff()
    def wilder(series):
        seeded = series.iloc[13:].copy()
        seeded.iloc[0] = float(series.iloc[:14].mean())
        return seeded.ewm(alpha=1 / 14, adjust=False).mean()
    gain = wilder(delta.iloc[1:].clip(lower=0)).iloc[-1]
    loss = wilder((-delta.iloc[1:]).clip(lower=0)).iloc[-1]
    rsi = 100 - 100 / (1 + gain / loss) if loss > 1e-12 else (100 if gain > 0 else 50)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    up, down = high.diff(), -low.diff()
    plus = wilder(up.where((up > down) & (up > 0), 0.0).iloc[1:])
    minus = wilder(down.where((down > up) & (down > 0), 0.0).iloc[1:])
    atr = wilder(tr.iloc[1:]).replace(0, float("nan"))
    plus, minus = (100 * plus / atr).fillna(0), (100 * minus / atr).fillna(0)
    dx = (100 * (plus - minus).abs() / (plus + minus).replace(0, float("nan"))).fillna(0)
    adx = wilder(dx).iloc[-1]
    return {"rsi": float(rsi), "adx": float(adx),
            "ema_price": float(close.ewm(span=21, adjust=False).mean().iloc[-1]),
            "ema_fast": float(close.ewm(span=9, adjust=False).mean().iloc[-1])}


def volume_profile(frame, now, daily, mode="intraday", sample_limit=20, minimum=10):
    """Compare completed cumulative five-minute slots, never partial vs full slots."""
    prior = complete_bars(daily, now, daily=True)
    if mode == "regular":
        volumes = list(prior[prior["volume"] > 0]["volume"].tail(sample_limit))
        return {"expected": median(volumes) if len(volumes) >= minimum else None,
                "sessions": len(volumes), "observed": None, "cutoff": None}
    reference = now.date()
    end = now.replace(hour=9, minute=15, second=0, microsecond=0)
    slots = min(75, max(0, int((now - end).total_seconds() // 300)))
    if not market_open(now):
        completed = complete_bars(frame, now)
        if completed.empty:
            return {"expected": None, "sessions": 0, "observed": None, "cutoff": None}
        reference = completed.iloc[-1]["date"].date()
        end = datetime.combine(reference, OPEN, IST)
        slots = 75
    cutoff = end + timedelta(minutes=slots * 5)
    if slots == 0 or frame.empty:
        return {"expected": None, "sessions": 0, "observed": None, "cutoff": cutoff}
    sessions, current = [], None
    for day, session in frame.groupby(frame["date"].dt.date):
        start = datetime.combine(day, OPEN, IST)
        expected_slots = pd.date_range(start, periods=slots, freq="5min")
        portion = session.set_index("date").reindex(expected_slots)
        if portion["volume"].isna().any():
            continue
        total = float(portion["volume"].sum())
        if day == reference:
            current = total
        elif day < reference and total > 0:
            sessions.append((day, total))
    baselines = [volume for _, volume in sorted(sessions)[-sample_limit:]]
    return {"expected": median(baselines) if len(baselines) >= minimum else None,
            "sessions": len(baselines), "observed": current, "cutoff": cutoff}


def liquidity(tick, fresh, settings):
    ltp = number(tick.get("ltp")) or 0
    average = number(tick.get("average_price"))
    volume = number(tick.get("volume")) or 0
    depth = tick.get("depth") or {}
    buys = [level for level in depth.get("buy", [])[:5] if (number(level.get("quantity")) or 0) > 0 and (number(level.get("price")) or 0) > 0]
    sells = [level for level in depth.get("sell", [])[:5] if (number(level.get("quantity")) or 0) > 0 and (number(level.get("price")) or 0) > 0]
    bid = max((number(level["price"]) for level in buys), default=None)
    ask = min((number(level["price"]) for level in sells), default=None)
    spread = (ask - bid) / ((ask + bid) / 2) * 10000 if bid and ask and ask >= bid else None
    buy_value = sum(float(level["price"]) * float(level["quantity"]) for level in buys)
    sell_value = sum(float(level["price"]) * float(level["quantity"]) for level in sells)
    depth_value = buy_value + sell_value
    turnover = volume * average if average and average > 0 else None
    valid = turnover is not None and bid is not None and ask is not None and spread is not None
    eligible = bool(fresh and valid and turnover >= settings["min_turnover"] and
                    spread <= settings["max_spread_bps"] and min(buy_value, sell_value) >= settings["min_side_depth"])
    score = None
    if valid:
        value_score = min(1, max(0, math.log10(max(turnover, 1) / 1e6) / 4))
        depth_score = min(1, max(0, math.log10(max(min(buy_value, sell_value), 1) / 1e4) / 3))
        score = 100 * (0.60 * value_score + 0.25 * max(0, 1 - spread / 20) + 0.15 * depth_score)
    reason = "Eligible traded value, two-sided depth and spread" if eligible else "Stale quote or insufficient traded value, two-sided depth or spread"
    return {"bid": bid, "ask": ask, "spread_bps": spread, "depth_value": depth_value,
            "turnover": turnover, "liquidity_score": score, "liquidity_eligible": eligible,
            "liquidity_reason": reason, "book_imbalance": (buy_value - sell_value) / depth_value if depth_value else None}


def quote_fresh(tick, now, stale_sec):
    source, received = timestamp(tick.get("source_at")), timestamp(tick.get("received_at"))
    return bool(market_open(now) and source is not None and received is not None and
                source.date() == now.date() and 0 <= (now - source).total_seconds() <= stale_sec and
                0 <= (now - received).total_seconds() <= stale_sec)


def build_row(symbol, sector, history, tick, now, settings, mode="intraday"):
    five = complete_bars(history.get("intraday", normalize([])), now)
    daily = complete_bars(history.get("regular", normalize([])), now, daily=True)
    frame = five if mode == "intraday" else daily
    ltp = number(tick.get("ltp"))
    if ltp is None and frame.empty:
        return None
    ltp = ltp or float(frame.iloc[-1]["close"])
    ohlc = tick.get("ohlc") or {}
    day = timestamp(tick.get("source_at"))
    session_date = day.date() if day is not None else frame.iloc[-1]["date"].date()
    session = five[five["date"].dt.date == session_date] if not five.empty else five
    opening = number(ohlc.get("open")) or (float(session.iloc[0]["open"]) if not session.empty else None)
    preceding = daily[daily["date"].dt.date < session_date] if not daily.empty else daily
    baseline = (float(preceding.iloc[-1]["close"]) if not preceding.empty else number(ohlc.get("close"))) if mode == "regular" else opening
    change = (ltp / baseline - 1) * 100 if baseline else None
    if change is None:
        return None
    fresh = quote_fresh(tick, now, settings["quote_stale_sec"])
    values = indicators(frame)
    current = five[five["date"].dt.date == now.date()] if not five.empty else five
    feature_at = (frame.iloc[-1]["date"] + pd.Timedelta(minutes=5)) if mode == "intraday" and not frame.empty else (frame.iloc[-1]["date"] if not frame.empty else None)
    current_features = not current.empty and (now - (current.iloc[-1]["date"] + pd.Timedelta(minutes=5))).total_seconds() < 360
    vwap = None
    if fresh and current_features:
        vwap = number(tick.get("average_price"))
        if vwap is None and current["volume"].sum() > 0:
            typical = (current["high"] + current["low"] + current["close"]) / 3
            vwap = float((typical * current["volume"]).sum() / current["volume"].sum())
    profile = volume_profile(five, now, daily, mode, settings["volume_sessions"], settings["min_volume_sessions"])
    observed = number(tick.get("volume")) if mode == "regular" else profile["observed"]
    ratio = observed / profile["expected"] if observed is not None and profile["expected"] else None
    recent = (float(session.iloc[-1]["close"]) / float(session.iloc[max(0, len(session) - 3)]["open"]) - 1) * 100 if len(session) >= 2 else None
    ema_price, fast = values.pop("ema_price"), values.pop("ema_fast")
    ema_gap = (ltp / ema_price - 1) * 100 if ema_price else None
    trend5 = (fast / ema_price - 1) * 100 if fast and ema_price else None
    fifteens = pd.Series(dtype=float)
    if not five.empty:
        grouped = five.groupby(five["date"].dt.floor("15min"))["close"].agg(["last", "count"])
        fifteens = grouped.loc[grouped["count"] == 3, "last"]
    trend15 = None
    if len(fifteens) >= 21:
        series = pd.Series(fifteens)
        trend15 = (series.ewm(span=9, adjust=False).mean().iloc[-1] / series.ewm(span=21, adjust=False).mean().iloc[-1] - 1) * 100
    atr_percent = None
    if len(daily) >= 21:
        tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - daily["close"].shift()).abs(), (daily["low"] - daily["close"].shift()).abs()], axis=1).max(axis=1)
        atr_percent = float((tr / daily["close"] * 100).tail(20).mean())
    side = 1 if change >= 0 else -1
    continuity = float((((session["close"] - session["open"]) * side) > 0).tail(3).mean()) if len(session) >= 3 else None
    ready = values["rsi"] is not None and ratio is not None and atr_percent is not None
    score = None
    if ready:
        clip = lambda value: max(0.0, min(1.0, value))
        score = 100 * (0.25 * clip(abs(change) / max(atr_percent, .1)) +
                       0.25 * clip(ratio / 3) + 0.20 * (continuity or 0) +
                       0.20 * clip((values["adx"] or 0) / 40) * int((trend5 or 0) * side > 0) +
                       0.10 * clip((recent or 0) * side / .5))
    result = {"symbol": symbol, "display": symbol, "sector": sector, "isIndex": False,
              "ltp": ltp, "change": change, "direction": side, "recent": recent,
              "ratio": ratio, "timeVolumeRatio": ratio if mode == "intraday" else None,
              "volume_baseline_sessions": profile["sessions"], "volume_cutoff": profile["cutoff"].isoformat() if profile["cutoff"] else None,
              "vwap": vwap, "vwapGap": (ltp / vwap - 1) * 100 if vwap else None,
              "ema": ema_gap, "emaTrend5": trend5, "emaTrend15": number(trend15),
              "atrPercent": atr_percent, "trendQuality": continuity, "score": score,
              "rfactor": None, "indicators_ready": ready, "fresh": fresh,
              "quote_as_of": day.isoformat() if day is not None else None,
              "feature_as_of": feature_at.isoformat() if feature_at is not None else None,
              "feature_mode": mode, "baseline_date": preceding.iloc[-1]["date"].date().isoformat() if mode == "regular" and not preceding.empty else None,
              "session_date": session_date.isoformat(), "volatility": "high" if abs(change) >= 2.5 else "medium" if abs(change) >= 1 else "low",
              "booster": None, **values, **liquidity(tick, fresh, settings)}
    result["_session"] = current
    result["_current_features"] = bool(current_features)
    result["_ema_price"] = ema_price
    result["_baseline"] = baseline
    result["_expected_volume"] = profile["expected"]
    return result


def refresh_row(original, tick, now, settings, mode="intraday"):
    """Refresh price/depth cheaply; closed-bar features change only on new bars/backfill."""
    row = dict(original)
    price = number(tick.get("ltp")) or row["ltp"]
    source = timestamp(tick.get("source_at"))
    baseline = number((tick.get("ohlc") or {}).get("open")) if mode == "intraday" else row["_baseline"]
    baseline = baseline or row["_baseline"]
    change = (price / baseline - 1) * 100 if baseline else row["change"]
    side = 1 if change >= 0 else -1
    row.update(ltp=price, change=change, direction=side, fresh=quote_fresh(tick, now, settings["quote_stale_sec"]),
               quote_as_of=source.isoformat() if source is not None else row["quote_as_of"],
               session_date=source.date().isoformat() if source is not None else row["session_date"], booster=None,
               volatility="high" if abs(change) >= 2.5 else "medium" if abs(change) >= 1 else "low")
    if row["_ema_price"]:
        row["ema"] = (price / row["_ema_price"] - 1) * 100
    if mode == "regular" and row["_expected_volume"]:
        volume = number(tick.get("volume"))
        row["ratio"] = volume / row["_expected_volume"] if volume is not None else None
    if row["fresh"] and row["_current_features"]:
        row["vwap"] = number(tick.get("average_price")) or row["vwap"]
        row["vwapGap"] = (price / row["vwap"] - 1) * 100 if row["vwap"] else None
    else:
        row["vwap"], row["vwapGap"] = None, None
    session = row["_session"]
    continuity = float((((session["close"] - session["open"]) * side) > 0).tail(3).mean()) if len(session) >= 3 else 0
    row["trendQuality"] = continuity
    if row["indicators_ready"] and row["ratio"] is not None:
        clip = lambda value: max(0.0, min(1.0, value))
        row["score"] = 100 * (.25 * clip(abs(change) / max(row["atrPercent"], .1)) + .25 * clip(row["ratio"] / 3) +
                              .20 * continuity + .20 * clip(row["adx"] / 40) * int((row["emaTrend5"] or 0) * side > 0) +
                              .10 * clip((row["recent"] or 0) * side / .5))
    row.update(liquidity(tick, row["fresh"], settings))
    return row


def sector_flow(rows):
    groups = {}
    for row in rows:
        if row.get("isIndex") or not row.get("fresh"):
            continue
        groups.setdefault(row["sector"], []).append(row)
    results = []
    for name, children in groups.items():
        ratios = [row["ratio"] for row in children if row.get("ratio") is not None]
        scores = [row["score"] * row["direction"] for row in children if row.get("score") is not None]
        results.append({"name": name, "mean": sum(row["change"] for row in children) / len(children),
                        "volume_ratio_mean": sum(ratios) / len(ratios) if ratios else None,
                        "dirRScore": sum(scores) / len(scores) if scores else None,
                        "count": len(children), "up": sum(row["change"] > 0 for row in children),
                        "down": sum(row["change"] < 0 for row in children)})
    return sorted(results, key=lambda item: item["mean"], reverse=True)


def market_context(rows, index_tick, members, now, settings):
    constituents = [row for row in rows if row["symbol"] in members and row["fresh"]]
    coverage = len(constituents) / max(1, len(members))
    features = [row for row in constituents if row["_current_features"] and row["vwap"] is not None and row["emaTrend5"] is not None]
    opening, price = number((index_tick.get("ohlc") or {}).get("open")), number(index_tick.get("ltp"))
    change = (price / opening - 1) * 100 if opening and price else None
    as_of = min((row["quote_as_of"] for row in constituents), default=None)
    index_at = timestamp(index_tick.get("source_at"))
    if index_at is not None and as_of is not None:
        as_of = min(as_of, index_at.isoformat())
    result = {"regime": "insufficient_data", "coverage": coverage, "feature_coverage": len(features) / max(1, len(members)),
              "agreement": None, "change": change, "as_of": as_of, "session_date": now.date().isoformat(),
              "votes": {}, "note": "Heuristic agreement, not a probability of profit"}
    if not market_open(now):
        result["regime"] = "stale"
        return result
    if coverage < .8 or len(features) / max(1, len(members)) < .8 or not quote_fresh(index_tick, now, settings["quote_stale_sec"]):
        return result
    vote = lambda value, low, high: 1 if value > high else -1 if value < low else 0
    breadth = sum(row["change"] > 0 for row in constituents) / len(constituents)
    vwap_breadth = sum(row["ltp"] > row["vwap"] for row in features) / len(features)
    trend_breadth = sum(row["emaTrend5"] > 0 for row in features) / len(features)
    groups = sector_flow(constituents)
    sector_breadth = sum(group["mean"] > 0 for group in groups) / max(1, len(groups))
    votes = {"index_move": vote(change, -.03, .03), "price_breadth": vote(breadth, .45, .55),
             "vwap_breadth": vote(vwap_breadth, .45, .55), "ema_breadth": vote(trend_breadth, .45, .55),
             "sector_breadth": vote(sector_breadth, .45, .55)}
    bulls, bears = list(votes.values()).count(1), list(votes.values()).count(-1)
    result.update(regime="bullish" if bulls >= 3 and bears <= 1 else "bearish" if bears >= 3 and bulls <= 1 else "neutral",
                  agreement=round(max(bulls, bears) / len(votes) * 100), votes=votes,
                  breadth=breadth, vwap_breadth=vwap_breadth)
    return result


def attach_boosters(rows, context, now, settings):
    sectors = {group["name"]: group["mean"] for group in sector_flow(rows)}
    for row in rows:
        side = row["direction"]
        base = {"status": "watchlist", "side": "long" if side > 0 else "short",
                "reason": "Waiting for a fresh, confirmed opening-range setup"}
        row["booster"] = base
        session = row.get("_session")
        if not row["fresh"] or not row["indicators_ready"] or not row["_current_features"]:
            continue
        if not row["liquidity_eligible"] or context["regime"] != ("bullish" if side > 0 else "bearish"):
            continue
        if not market_open(now) or now.time() >= time(14, 45) or len(session) < 4:
            continue
        opening = datetime.combine(now.date(), OPEN, IST)
        expected = pd.date_range(opening, periods=len(session), freq="5min")
        if list(session["date"]) != list(expected):
            base["reason"] = "Current-session candle coverage is incomplete"
            continue
        if row["volume_baseline_sessions"] < settings["volume_sessions"] or (row["ratio"] or 0) < settings["booster_rvol"]:
            continue
        if (row["recent"] or 0) * side <= 0 or (row["emaTrend5"] or 0) * side <= 0 or (row["emaTrend15"] or 0) * side <= 0:
            continue
        if not row["vwap"] or (row["ltp"] - row["vwap"]) * side <= 0 or sectors.get(row["sector"], 0) * side <= 0:
            continue
        opening_range = session.iloc[:3]
        trigger = float(opening_range["high"].max() if side > 0 else opening_range["low"].min())
        last, previous = session.iloc[-1], session.iloc[-2]
        crossed = (float(last["close"]) - trigger) * side > 0 and (float(previous["close"]) - trigger) * side <= 0
        base["status"] = "forming"
        if not crossed:
            continue
        stop = float(last["low"] if side > 0 else last["high"])
        risk = (trigger - stop) * side
        risk_percent = risk / trigger * 100 if trigger else 0
        extension = (row["ltp"] - trigger) * side
        end = last["date"] + pd.Timedelta(minutes=5)
        expiry = end + timedelta(seconds=settings["signal_ttl_sec"])
        if risk <= 0 or not .10 <= risk_percent <= 1.0 or extension < 0 or extension > risk * .5 or now >= expiry:
            base.update(status="invalidated", reason="Expired, reversed or too extended beyond the trigger")
            continue
        base.update(status="eligible", trigger=trigger, stop=stop, target=trigger + side * risk * 1.5,
                    expires_at=expiry.isoformat(), signal_at=end.isoformat(),
                    id=f"{now.date()}:{row['symbol']}:{side}:{end.isoformat()}",
                    reason="Closed-bar opening-range breakout; RVOL, trend, sector and market aligned")


def public_row(row):
    def clean(value):
        if isinstance(value, float):
            return round(value, 4) if math.isfinite(value) else None
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if not key.startswith("_")}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(row)


def basket_rows(rows, groups):
    results = []
    for name, symbols in groups.items():
        children = [row for row in rows if row["symbol"] in symbols]
        if not children:
            continue
        average = lambda key: sum(row[key] for row in children if row.get(key) is not None) / max(1, sum(row.get(key) is not None for row in children))
        result = {"symbol": name, "display": name + " basket", "sector": "BASKET", "isIndex": True,
                  "ltp": None, "change": average("change"), "direction": 1 if average("change") >= 0 else -1,
                  "ratio": average("ratio") if any(row.get("ratio") is not None for row in children) else None,
                  "score": average("score") if any(row.get("score") is not None for row in children) else None,
                  "rsi": None, "adx": None, "ema": None, "rfactor": None, "vwap": None,
                  "fresh": False, "indicators_ready": False, "quote_as_of": None, "feature_as_of": None,
                  "session_date": children[0]["session_date"], "volatility": "medium", "booster": None,
                  "liquidity_eligible": False, "note": "Equal-weight custom basket, not an official index level"}
        results.append(result)
    return results
