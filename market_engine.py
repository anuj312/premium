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
    frame = frame[COLUMNS + (["source"] if "source" in frame.columns else [])].copy()
    if "source" in frame:
        frame["source"] = frame["source"].where(frame["source"].isin(["broker_history", "sampled_ticks"]), "unknown")
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
    depth = tick.get("depth") if isinstance(tick.get("depth"), dict) else {}
    sides = [depth.get(key) if isinstance(depth.get(key), list) else [] for key in ("buy", "sell")]
    buys, sells = [[level for level in levels[:5] if isinstance(level, dict) and
                   (number(level.get("quantity")) or 0) > 0 and (number(level.get("price")) or 0) > 0] for levels in sides]
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
    buy_quantity = sum(float(level["quantity"]) for level in buys)
    sell_quantity = sum(float(level["quantity"]) for level in sells)
    total_quantity = buy_quantity + sell_quantity
    malformed = any(not isinstance(level, dict) or number(level.get("price")) is None or
                    number(level.get("quantity")) is None or number(level.get("price")) < 0 or
                    number(level.get("quantity")) < 0 or
                    (number(level.get("price")) == 0 and number(level.get("quantity")) > 0) or
                    isinstance(level.get("price"), bool) or isinstance(level.get("quantity"), bool)
                    for levels in sides for level in levels[:5])
    book_valid = bool(fresh and buys and sells and spread is not None and not malformed)
    return {"bid": bid, "ask": ask, "spread_bps": spread, "depth_value": depth_value,
             "turnover": turnover, "liquidity_score": score, "liquidity_eligible": eligible,
             "liquidity_reason": reason, "book_imbalance": (buy_value - sell_value) / depth_value if depth_value else None,
             "depth_valid": book_valid, "depth_bid_quantity": buy_quantity, "depth_ask_quantity": sell_quantity,
             "depth_bid_levels": len(buys), "depth_ask_levels": len(sells),
             "depth_quantity_imbalance": (buy_quantity - sell_quantity) / total_quantity if book_valid else None,
             "depth_as_of": tick.get("source_at"), "depth_received_at": tick.get("received_at")}


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
              "booster": None, "building": None, **values, **liquidity(tick, fresh, settings)}
    result["_session"] = current
    result["_current_features"] = bool(current_features)
    result["_ema_price"] = ema_price
    result["_baseline"] = baseline
    result["_expected_volume"] = profile["expected"]
    result["_imbalance_features"] = imbalance_features(five, now, settings, allow_early=True) if mode == "intraday" else None
    result["quote_received_at"] = tick.get("received_at")
    result["live_vwap"] = number(tick.get("average_price")) if fresh else None
    result["imbalance"] = None
    result["session_pressure"] = None
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
                  session_date=source.date().isoformat() if source is not None else row["session_date"], booster=None, building=None, imbalance=None, session_pressure=None,
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
    row["quote_received_at"] = tick.get("received_at")
    row["live_vwap"] = number(tick.get("average_price")) if row["fresh"] else None
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


def attach_building(rows, context, now, settings):
    """Near-boundary watchlist with explicit readiness, never an entry signal."""
    context_at = timestamp(context.get("as_of"))
    valid_context = bool(context.get("regime") in {"bullish", "bearish", "neutral"} and
                         context.get("session_date") == now.date().isoformat() and
                         context_at is not None and context_at.date() == now.date() and
                         0 <= (now - context_at).total_seconds() <= settings["quote_stale_sec"])
    sectors = {group["name"]: group["mean"] for group in sector_flow(
        [row for row in rows if row.get("direction") in (1, -1)])}
    opening = datetime.combine(now.date(), OPEN, IST)
    slots = int((now - opening).total_seconds() // 300)
    cutoff = datetime.combine(now.date(), time(14, 45), IST)
    for row in rows:
        row["building"] = None
        if not valid_context or not market_open(now) or now >= cutoff or slots < 3:
            continue
        if (row.get("isIndex") or row.get("feature_mode") != "intraday" or
                row.get("session_date") != now.date().isoformat() or
                not all(row.get(key) for key in ("fresh", "indicators_ready", "_current_features", "liquidity_eligible"))):
            continue
        side, price, vwap = row.get("direction"), number(row.get("ltp")), number(row.get("vwap"))
        trend5, ratio = number(row.get("emaTrend5")), number(row.get("ratio"))
        baseline = number(row.get("volume_baseline_sessions"))
        quote_at = timestamp(row.get("quote_as_of"))
        if (side not in (1, -1) or price is None or price <= 0 or vwap is None or vwap <= 0 or
                trend5 is None or trend5 * side <= 0 or (price - vwap) * side <= 0 or
                ratio is None or ratio < settings["building_min_rvol"] or
                baseline is None or baseline < settings["min_volume_sessions"] or
                quote_at is None or quote_at.date() != now.date() or
                not 0 <= (now - quote_at).total_seconds() <= settings["quote_stale_sec"]):
            continue
        session = row.get("_session")
        if not isinstance(session, pd.DataFrame) or not set(COLUMNS).issubset(session.columns):
            continue
        expected = pd.date_range(opening, periods=slots, freq="5min")
        if list(session["date"]) != list(expected):
            continue
        feature_at = timestamp(row.get("feature_as_of"))
        end = opening + timedelta(minutes=slots * 5)
        if feature_at is None or feature_at != end:
            continue
        first = session.iloc[:3]
        high, low = number(first["high"].max()), number(first["low"].min())
        if high is None or low is None or low <= 0 or high <= low or not low <= price <= high:
            continue
        trigger = high if side > 0 else low
        gap = (trigger - price) * side / trigger * 100
        if not 0 <= gap <= settings["building_max_gap_pct"] + 1e-9:
            continue
        # A failed/re-entered confirmed breakout is not a first pre-breakout buildup.
        if ((session.iloc[3:]["close"] - trigger) * side > 0).any():
            continue
        expiry = min(quote_at + timedelta(seconds=settings["quote_stale_sec"]),
                     context_at + timedelta(seconds=settings["quote_stale_sec"]),
                     now + timedelta(seconds=settings["cache_stale_sec"]),
                     end + timedelta(minutes=5), cutoff)
        if expiry <= now:
            continue
        trend15, recent = number(row.get("emaTrend15")), number(row.get("recent"))
        sector = number(sectors.get(row.get("sector")))
        checks = [
            {"key": "volume", "label": "Volume", "passed": bool(ratio >= settings["booster_rvol"] and baseline >= settings["volume_sessions"]),
             "detail": f"RVOL {ratio:.2f}x / needs {settings['booster_rvol']:.2f}x; {int(baseline)}/{settings['volume_sessions']} sessions"},
            {"key": "vwap", "label": "VWAP", "passed": True,
             "detail": f"{'Above' if side > 0 else 'Below'} Rs {vwap:.2f}"},
            {"key": "trend5", "label": "5m trend", "passed": True,
             "detail": f"EMA9 {'>' if side > 0 else '<'} EMA21"},
            {"key": "trend15", "label": "15m trend", "passed": bool(trend15 is not None and trend15 * side > 0),
             "detail": f"EMA9/21 gap {trend15:+.3f}%" if trend15 is not None else "Completed-bar trend unavailable"},
            {"key": "recent", "label": "Recent move", "passed": bool(recent is not None and recent * side > 0),
             "detail": f"{recent:+.2f}% on completed bars" if recent is not None else "Recent movement unavailable"},
            {"key": "sector", "label": "Sector", "passed": bool(sector is not None and sector * side > 0),
             "detail": f"{row['sector']}: {sector:+.2f}%" if sector is not None else "Sector direction unavailable"},
            {"key": "market", "label": "Market", "passed": context["regime"] == ("bullish" if side > 0 else "bearish"),
             "detail": "Balanced" if context["regime"] == "neutral" else context["regime"].capitalize()},
        ]
        row["building"] = {"status": "building", "side": "long" if side > 0 else "short",
                           "trigger": trigger, "range_low": low, "range_high": high, "gap_pct": gap,
                           "passed": sum(check["passed"] for check in checks), "total": len(checks), "checks": checks,
                           "as_of": now.isoformat(), "feature_as_of": feature_at.isoformat(), "expires_at": expiry.isoformat(),
                           "reason": f"Waiting for a completed 5m close {'above' if side > 0 else 'below'} the opening range; not an entry signal"}


def valid_pressure_bar(bar):
    values = [number(bar.get(key)) for key in COLUMNS[1:]]
    if any(value is None or isinstance(bar.get(key), bool) for key, value in zip(COLUMNS[1:], values)):
        return False
    opening, high, low, close, volume = values
    return bool(low > 0 and low <= min(opening, close) <= max(opening, close) <= high and volume >= 0)


def imbalance_features(five, now, settings, allow_early=False):
    """Closed-bar estimates, not aggressor-classified executions."""
    if five.empty or not market_open(now):
        return None
    opening = datetime.combine(now.date(), OPEN, IST)
    slots = int((now - opening).total_seconds() // 300)
    if slots < (1 if allow_early else 3):
        return None
    if allow_early:
        five = complete_bars(five, now)
    current = five[five["date"].dt.date == now.date()]
    expected = list(pd.date_range(opening, periods=slots, freq="5min"))
    if list(current["date"]) != expected:
        return None
    bars = current.to_dict("records")
    if not all(valid_pressure_bar(bar) for bar in bars):
        return None
    sources = {bar.get("source", "unknown") for bar in bars}
    if not sources.issubset({"broker_history", "sampled_ticks"}):
        return None
    last = bars[-1]
    volume, span = float(last["volume"]), float(last["high"] - last["low"])
    fraction = (last["close"] - last["low"]) / span if span > 0 else .5
    buy = volume * fraction
    estimate = (2 * fraction - 1) * 100 if volume > 0 else None
    side = 1 if estimate is not None and estimate > 0 else -1 if estimate is not None and estimate < 0 else 0
    prior = five[(five["date"].dt.date < now.date()) & (five["date"].dt.time == last["date"].time())]
    baseline = [float(bar["volume"]) for bar in prior.to_dict("records")
                if valid_pressure_bar(bar) and bar.get("source") == "broker_history"][-settings["volume_sessions"]:]
    typical = [(bar["high"] + bar["low"] + bar["close"]) / 3 for bar in bars]
    total = number(sum(bar["volume"] for bar in bars))
    weighted = number(sum(price * bar["volume"] for price, bar in zip(typical, bars)))
    if total is None or total <= 0 or weighted is None:
        return None
    fractions = [(bar["close"] - bar["low"]) / (bar["high"] - bar["low"])
                 if bar["high"] > bar["low"] else .5 for bar in bars]
    buys = [bar["volume"] * fraction for bar, fraction in zip(bars, fractions)]
    sells = [bar["volume"] - buy for bar, buy in zip(bars, buys)]
    session_buy, session_sell = number(sum(buys)), number(sum(sells))
    if session_buy is None or session_sell is None:
        return None
    net = session_buy - session_sell
    session_pct = net / total * 100
    session_side = 1 if session_pct > 0 else -1 if session_pct < 0 else 0
    vwap = weighted / total
    before_volume = sum(bar["volume"] for bar in bars[:-1])
    previous_pct = (sum(buys[:-1]) - sum(sells[:-1])) / before_volume * 100 if before_volume > 0 else None
    change_pp = session_pct - previous_pct if previous_pct is not None else None
    before_vwap = sum(price * bar["volume"] for price, bar in zip(typical[:-1], bars[:-1])) / before_volume if before_volume > 0 else None
    alignments = []
    for direction in (side, session_side):
        consecutive = 0
        for bar in reversed(bars):
            if direction == 0 or (bar["close"] - bar["open"]) * direction <= 0:
                break
            consecutive += 1
        level = (max(bar["high"] for bar in bars[:3]) if direction > 0 else
                 min(bar["low"] for bar in bars[:3])) if slots >= 3 else None
        crossed = bool(direction and slots >= 4 and (last["close"] - level) * direction > 0 and
                       (bars[-2]["close"] - level) * direction <= 0)
        alignments.append({"side": direction, "consecutive": consecutive, "breakout": crossed, "breakout_level": level})
    expected_volume = number(median(baseline)) if len(baseline) >= settings["min_volume_sessions"] else None
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    end = opening + timedelta(minutes=slots * 5)
    return {"side": side, "feature_as_of": end.isoformat(),
            "session": {"start": opening.isoformat(), "end": end.isoformat(), "bars": slots, "volume": total,
                        "estimated_buy_volume": session_buy, "estimated_sell_volume": session_sell,
                        "estimated_net_volume": net, "estimated_imbalance_pct": session_pct,
                        "previous_imbalance_pct": previous_pct, "change_pp": change_pp,
                        "building": bool(session_side and previous_pct is not None and
                                         previous_pct * session_side >= 0 and change_pp * session_side > 0),
                        "source": source, "quality": "broker_confirmed" if sources == {"broker_history"} else "sampled",
                        "method": "volume_weighted_close_location", "actual_buy_volume": None,
                        "actual_sell_volume": None, "actual_imbalance_pct": None},
            "_session_alignment": alignments[1],
            "candle": {"source": source, "quality": "broker_confirmed" if sources == {"broker_history"} else "sampled",
                       "start": last["date"].isoformat(), "end": end.isoformat(),
                       **{key: float(last[key]) for key in COLUMNS[1:]},
                       "estimated_buy_volume": buy, "estimated_sell_volume": volume - buy,
                       "estimated_imbalance_pct": estimate, "actual_buy_volume": None,
                       "actual_sell_volume": None, "actual_imbalance_pct": None, "method": "close_location"},
            "bar_volume_ratio": volume / expected_volume if expected_volume and expected_volume > 0 else None,
            "baseline_sessions": len(baseline), "closed_vwap_estimate": vwap,
            "closed_vwap_gap_pct": (last["close"] / vwap - 1) * 100,
            "vwap_slope_pct": (vwap / before_vwap - 1) * 100 if before_vwap else None,
            "momentum_pct": (last["close"] / bars[-3]["close"] - 1) * 100 if slots >= 3 else None,
            **{key: value for key, value in alignments[0].items() if key != "side"}}


def imbalance_active(row, now, settings, live=True, signal_key="imbalance"):
    signal = row.get(signal_key)
    if not live or not signal or signal.get("status") not in {"watch", "confirmed", "provisional"}:
        return False
    end, expires = timestamp(signal.get("feature_as_of")), timestamp(signal.get("expires_at"))
    opening = datetime.combine(now.date(), OPEN, IST)
    cutoff = opening + timedelta(minutes=5 * max(0, int((now - opening).total_seconds() // 300)))
    source, received = timestamp(row.get("quote_as_of")), timestamp(row.get("quote_received_at"))
    computed = timestamp(signal.get("as_of"))
    alert = signal.get("alert") or {}
    if alert.get("eligible"):
        alert_end = timestamp(alert.get("expires_at"))
        level, price = number(signal.get("breakout_level")), number(row.get("ltp"))
        direction = 1 if signal.get("side") == "buy" else -1
        if (signal.get("status") != "confirmed" or alert_end is None or now >= alert_end or
                level is None or price is None or (price - level) * direction <= 0):
            return False
    return bool(market_open(now) and row.get("fresh") and not row.get("isIndex") and
                row.get("feature_mode") == "intraday" and row.get("session_date") == now.date().isoformat() and
                end is not None and end == cutoff and expires is not None and now < expires and
                all(at is not None and at.date() == now.date() and 0 <= (now - at).total_seconds() <= settings["quote_stale_sec"]
                    for at in (source, received)) and computed is not None and
                0 <= (now - computed).total_seconds() <= settings["cache_stale_sec"])


def attach_imbalance(rows, now, settings, membership, horizon="candle"):
    """Rank estimated pressure, preserving the shipped five-minute hard gates."""
    if horizon not in {"candle", "session"}:
        raise ValueError("Pressure horizon must be candle or session")
    cumulative = horizon == "session"
    signal_key = "session_pressure" if cumulative else "imbalance"
    score_version = "session-pressure-v1" if cumulative else "pressure-v1"
    stale = settings["quote_stale_sec"]
    depth_min = settings.get("imbalance_min_pct", 20)
    spike_min = settings.get("imbalance_min_bar_rvol", 1.5)
    for row in rows:
        row[signal_key] = None
        feature = row.get("_imbalance_features")
        if (not feature or row.get("isIndex") or row.get("feature_mode") != "intraday" or
                not row.get("fresh") or not row.get("liquidity_eligible") or not row.get("depth_valid")):
            continue
        if not cumulative and feature["session"]["bars"] < 3:
            continue
        if cumulative:
            feature = {**feature, **feature["_session_alignment"]}
        pressure = feature["session" if cumulative else "candle"]
        side, estimate = feature["side"], number(pressure["estimated_imbalance_pct"])
        book = number(row.get("depth_quantity_imbalance"))
        ratio = number(feature.get("bar_volume_ratio"))
        if (side == 0 or estimate is None or estimate * side < depth_min or book is None or
                (not cumulative and (book * side * 100 < depth_min or ratio is None or ratio < spike_min))):
            continue
        source, received = timestamp(row.get("depth_as_of")), timestamp(row.get("depth_received_at"))
        if not all(at is not None and at.date() == now.date() and 0 <= (now - at).total_seconds() <= stale for at in (source, received)):
            continue
        peers = {symbol for symbol, sector in membership.items() if sector == row["sector"] and symbol != row["symbol"]}
        usable = []
        peer_times = []
        for child in rows:
            if child["symbol"] not in peers or not child.get("fresh") or number(child.get("change")) is None:
                continue
            times = [timestamp(child.get(key)) for key in ("quote_as_of", "quote_received_at")]
            if all(at is not None and at.date() == now.date() and 0 <= (now - at).total_seconds() <= stale for at in times):
                usable.append(child)
                peer_times.extend(times)
        coverage = len(usable) / len(peers) if peers else 0
        known_sector = bool(usable and coverage >= .8)
        sector_mean = sum(child["change"] for child in usable) / len(usable) if known_sector else None
        sector_at = min(peer_times) if known_sector else None
        sector = {"mean": sector_mean, "coverage": coverage, "peers": len(usable), "total_peers": len(peers),
                  "as_of": sector_at.isoformat() if sector_at else None, "definition": "excludes_subject"}
        gap, momentum, slope = feature["closed_vwap_gap_pct"], feature["momentum_pct"], feature["vwap_slope_pct"]
        volume_passed = bool(ratio >= spike_min) if ratio is not None and feature["baseline_sessions"] >= settings["min_volume_sessions"] else None
        momentum_known = momentum is not None and slope is not None
        momentum_passed = bool(momentum * side > 0 and slope * side > 0) if momentum_known else (None if cumulative else False)
        checks = [
            {"key": "depth", "label": "Quoted depth", "passed": bool(book * side * 100 >= depth_min), "weight": 20,
             "detail": f"Resting quantity imbalance {book * 100:+.1f}% / needs {side * depth_min:+.1f}%"},
            {"key": "session" if cumulative else "candle", "label": "Estimated session pressure" if cumulative else "Estimated candle pressure", "passed": True, "weight": 15,
              "detail": f"{'Volume-weighted session' if cumulative else 'Close-location'} estimate {estimate:+.1f}%; not actual executed buy/sell volume"},
            {"key": "volume", "label": "5m volume spike",
              "passed": volume_passed if cumulative else True, "weight": 15,
              "detail": f"Same-slot volume {ratio:.2f}x / needs {spike_min:.2f}x; {feature['baseline_sessions']} sessions" if ratio is not None else f"Same-slot baseline unavailable: {feature['baseline_sessions']} sessions"},
            {"key": "vwap", "label": "Closed-bar VWAP", "passed": bool(gap * side > 0), "weight": 10,
             "detail": f"Close vs HLC3 volume-weighted estimate {gap:+.3f}%"},
            {"key": "momentum", "label": "Momentum", "passed": momentum_passed, "weight": 10,
              "detail": f"Three-bar close return {momentum:+.3f}%; VWAP estimate slope {slope:+.3f}%" if momentum_known else ("Three-bar momentum or VWAP slope unavailable" if cumulative else "VWAP slope unavailable")},
            {"key": "breakout", "label": "Completed breakout", "passed": feature["breakout"] if feature["breakout_level"] is not None else None, "weight": 10,
              "detail": f"Latest close {'crossed' if feature['breakout'] else 'did not newly cross'} opening-range {'high' if side > 0 else 'low'} {feature['breakout_level']:.2f}" if feature["breakout_level"] is not None else "Three-bar opening range unavailable"},
            {"key": "consecutive", "label": "Consecutive candles", "passed": feature["consecutive"] >= 2, "weight": 10,
             "detail": f"{feature['consecutive']} consecutive {'bullish' if side > 0 else 'bearish'} bodies; needs 2"},
            {"key": "sector", "label": "Independent sector peers", "passed": bool(sector_mean * side > 0) if known_sector else None, "weight": 10,
             "detail": f"Peer mean {sector_mean:+.2f}% / coverage {coverage:.0%}; subject excluded" if known_sector else f"Unavailable: {len(usable)}/{len(peers)} fresh peers; needs 80% coverage"},
        ]
        end = timestamp(feature["feature_as_of"])
        expiry = min(source + timedelta(seconds=stale), received + timedelta(seconds=stale),
                     now + timedelta(seconds=settings["cache_stale_sec"]), end + timedelta(minutes=5),
                     datetime.combine(now.date(), CLOSE, IST))
        if sector_at:
            expiry = min(expiry, sector_at + timedelta(seconds=stale))
        if expiry <= now:
            continue
        passed = sum(check["passed"] is True for check in checks)
        alert_expiry = min(expiry, end + timedelta(seconds=settings["signal_ttl_sec"]))
        latest_estimate = number(feature["candle"]["estimated_imbalance_pct"])
        confirmed = bool(passed == 8 and pressure["quality"] == "broker_confirmed" and
                         (not cumulative or (latest_estimate is not None and latest_estimate * side >= depth_min)) and
                         (row["ltp"] - feature["breakout_level"]) * side > 0 and now < alert_expiry)
        row[signal_key] = {**{key: value for key, value in feature.items() if key != "side" and not key.startswith("_") and (cumulative or key != "session")},
            "status": "provisional" if pressure["quality"] != "broker_confirmed" else "confirmed" if confirmed else "watch",
            "side": "buy" if side > 0 else "sell", "score": sum(check["weight"] for check in checks if check["passed"] is True),
            "score_version": score_version, "passed": passed, "total": 8, "checks": checks,
            "as_of": now.isoformat(), "expires_at": expiry.isoformat(), "sector": sector,
            "live_vwap": row.get("live_vwap"), "vwap_source": "ohlcv_hlc3",
            "book": {"source": "broker_depth", "definition": "resting_quantity",
                     "bid_quantity": row["depth_bid_quantity"], "ask_quantity": row["depth_ask_quantity"],
                     "imbalance_pct": book * 100, "notional_imbalance_pct": row["book_imbalance"] * 100,
                     "bid_levels": row["depth_bid_levels"], "ask_levels": row["depth_ask_levels"],
                     "as_of": source.isoformat(), "received_at": received.isoformat()},
            "alert": {"id": f"{now.date()}:{row['symbol']}:{side}:{end.isoformat()}:{score_version}",
                      "eligible": confirmed, "expires_at": alert_expiry.isoformat()}}
        if not imbalance_active(row, now, settings, signal_key=signal_key):
            row[signal_key] = None


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
                  "fresh": False, "indicators_ready": False, "quote_as_of": None, "feature_as_of": None, "building": None,
                    "session_date": children[0]["session_date"], "volatility": "medium", "booster": None, "imbalance": None, "session_pressure": None,
                  "liquidity_eligible": False, "note": "Equal-weight custom basket, not an official index level"}
        results.append(result)
    return results
