import json
import math
import unittest
from datetime import timedelta
from statistics import median

from fixtures import (NOW, SETTINGS, candle, context_row, engine, history,
                      quote, server, session, signal_row, weekdays)


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        start = NOW - timedelta(days=1)
        self.bars = [candle(start + timedelta(minutes=5 * i),
                            close=100 + i * .06 + ((i * 7) % 13 - 6) * .25)
                     for i in range(64)]
        self.frame = engine.normalize(self.bars)

    def test_ema_uses_recursive_canonical_spans(self):
        values = engine.indicators(self.frame)
        for span, key in ((9, "ema_fast"), (21, "ema_price")):
            with self.subTest(span=span):
                expected = self.bars[0]["close"]
                for bar in self.bars[1:]:
                    expected += (bar["close"] - expected) * 2 / (span + 1)
                self.assertAlmostEqual(values[key], expected, places=10)

    def test_rsi_uses_wilder_initial_average_and_smoothing(self):
        differences = [b["close"] - a["close"]
                       for a, b in zip(self.bars, self.bars[1:])]
        gain = sum(max(delta, 0) for delta in differences[:14]) / 14
        loss = sum(max(-delta, 0) for delta in differences[:14]) / 14
        for delta in differences[14:]:
            gain = (gain * 13 + max(delta, 0)) / 14
            loss = (loss * 13 + max(-delta, 0)) / 14
        expected = 100 - 100 / (1 + gain / loss)
        self.assertAlmostEqual(engine.indicators(self.frame)["rsi"], expected, places=8)

    def test_adx_uses_wilder_initial_averages_and_smoothing(self):
        ranges, positives, negatives = [], [], []
        for before, after in zip(self.bars, self.bars[1:]):
            ranges.append(max(after["high"] - after["low"],
                              abs(after["high"] - before["close"]),
                              abs(after["low"] - before["close"])))
            up, down = after["high"] - before["high"], before["low"] - after["low"]
            positives.append(up if up > down and up > 0 else 0)
            negatives.append(down if down > up and down > 0 else 0)
        atr, plus, minus = (sum(values[:14]) / 14
                            for values in (ranges, positives, negatives))
        dx = []
        for i in range(13, len(ranges)):
            if i > 13:
                atr = (atr * 13 + ranges[i]) / 14
                plus = (plus * 13 + positives[i]) / 14
                minus = (minus * 13 + negatives[i]) / 14
            pdi, mdi = 100 * plus / atr, 100 * minus / atr
            dx.append(100 * abs(pdi - mdi) / (pdi + mdi))
        expected = sum(dx[:14]) / 14
        for value in dx[14:]:
            expected = (expected * 13 + value) / 14
        self.assertAlmostEqual(engine.indicators(self.frame)["adx"], expected, places=8)

    def test_warmup_does_not_invent_indicators(self):
        self.assertTrue(all(value is None
                            for value in engine.indicators(self.frame.iloc[:49]).values()))

    def test_flat_prices_have_neutral_rsi_and_zero_adx(self):
        frame = engine.normalize([candle(bar["date"]) for bar in self.bars])
        values = engine.indicators(frame)
        self.assertEqual(values["rsi"], 50)
        self.assertAlmostEqual(values["adx"], 0)


class PointInTimeTests(unittest.TestCase):
    def test_five_minute_close_boundary_excludes_forming_and_future_bars(self):
        boundary = NOW.replace(hour=10, minute=0, second=0)
        bars = engine.normalize([candle(boundary - timedelta(minutes=5)),
                                 candle(boundary), candle(boundary + timedelta(minutes=5))])
        self.assertEqual(list(engine.complete_bars(bars, boundary)["date"]),
                         [boundary - timedelta(minutes=5)])

    def test_current_and_future_daily_bars_are_excluded(self):
        bars = engine.normalize([candle(NOW - timedelta(days=3)),
                                 candle(NOW), candle(NOW + timedelta(days=1))])
        self.assertEqual(len(engine.complete_bars(bars, NOW, daily=True)), 1)

    def test_future_and_forming_bars_cannot_change_live_features(self):
        original = history()
        row = engine.build_row("SYNTH", "BANK", original, quote(), NOW, SETTINGS)
        contaminated = {key: frame.copy() for key, frame in original.items()}
        extra = [candle(NOW.replace(minute=0), close=9_000, volume=1e9),
                 candle(NOW + timedelta(minutes=5), close=8_000, volume=1e9)]
        contaminated["intraday"] = engine.normalize(
            original["intraday"].to_dict("records") + extra)
        contaminated["regular"] = engine.normalize(
            original["regular"].to_dict("records") + [candle(NOW, close=7_000)])
        actual = engine.build_row("SYNTH", "BANK", contaminated, quote(), NOW, SETTINGS)
        self.assertEqual(engine.public_row(actual), engine.public_row(row))
        self.assertEqual(actual["feature_as_of"], NOW.replace(minute=0, second=0).isoformat())

    def test_yesterdays_session_never_supplies_live_vwap(self):
        row = engine.build_row("SYNTH", "BANK", history(current_slots=0),
                               quote(average_price=None), NOW, SETTINGS)
        self.assertIsNone(row["vwap"])
        self.assertIsNone(row["vwapGap"])
        self.assertFalse(row["_current_features"])

    def test_fallback_vwap_uses_only_completed_current_session_bars(self):
        dataset = history(current_slots=0)
        current = session(NOW, [100] * 9, close=110)
        current[-1].update(high=114, low=109, close=112, volume=400)
        dataset["intraday"] = engine.normalize(
            dataset["intraday"].to_dict("records") + current)
        expected = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
                       for b in current) / sum(b["volume"] for b in current)
        row = engine.build_row("SYNTH", "BANK", dataset,
                               quote(average_price=None), NOW, SETTINGS)
        self.assertAlmostEqual(row["vwap"], expected)

    def test_regular_change_uses_last_completed_trading_close(self):
        dataset = history()
        dataset["regular"] = engine.normalize([
            candle(NOW - timedelta(days=4), close=80),
            candle(NOW - timedelta(days=3), close=90), candle(NOW, close=999)])
        row = engine.build_row("SYNTH", "BANK", dataset,
                               quote(ltp=99, ohlc={"open": 98, "close": 1}),
                               NOW, SETTINGS, "regular")
        self.assertAlmostEqual(row["change"], 10)

    def test_prior_session_regular_change_excludes_that_sessions_own_close(self):
        prior_session = NOW - timedelta(days=3)
        dataset = {"regular": engine.normalize([
            candle(prior_session - timedelta(days=1), close=80),
            candle(prior_session, close=88)])}
        row = engine.build_row("SYNTH", "BANK", dataset,
                               quote(prior_session, ltp=88, ohlc={"open": 85, "close": 80}),
                               NOW, SETTINGS, "regular")
        self.assertAlmostEqual(row["change"], 10)
        self.assertEqual(row["session_date"], prior_session.date().isoformat())
        self.assertEqual(row["baseline_date"],
                         (prior_session - timedelta(days=1)).date().isoformat())
        self.assertFalse(row["fresh"])

    def test_missing_daily_history_regular_change_uses_broker_previous_close(self):
        row = engine.build_row("SYNTH", "BANK", {},
                               quote(ltp=99, ohlc={"open": 98, "close": 90}),
                               NOW, SETTINGS, "regular")
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["change"], 10)
        self.assertIsNone(row["baseline_date"])

    def test_missing_daily_and_broker_previous_close_never_uses_today_open(self):
        row = engine.build_row("SYNTH", "BANK", {},
                               quote(ltp=99, ohlc={"open": 98}),
                               NOW, SETTINGS, "regular")
        self.assertIsNone(row)

    def test_intraday_change_uses_session_open_not_previous_close(self):
        row = engine.build_row("SYNTH", "BANK", history(),
                               quote(ltp=105), NOW, SETTINGS)
        self.assertAlmostEqual(row["change"], 5)

    def test_missing_current_volume_baseline_does_not_default_ratio_to_one(self):
        row = engine.build_row("SYNTH", "BANK", history(prior_sessions=9),
                               quote(), NOW, SETTINGS)
        self.assertIsNone(row["ratio"])
        self.assertIsNone(row["timeVolumeRatio"])
        self.assertIsNone(row["score"])
        self.assertFalse(row["indicators_ready"])

    def test_zero_completed_volume_returns_zero_ratio_not_missing_or_one(self):
        dataset = history(current_slots=0)
        dataset["intraday"] = engine.normalize(
            dataset["intraday"].to_dict("records") + session(NOW, [0] * 9))
        row = engine.build_row("SYNTH", "BANK", dataset, quote(), NOW, SETTINGS)
        self.assertEqual(row["ratio"], 0)
        self.assertEqual(row["timeVolumeRatio"], 0)

    def test_missing_market_data_does_not_invent_price(self):
        self.assertIsNone(engine.build_row("SYNTH", "BANK", {}, {}, NOW, SETTINGS))


class VolumeTests(unittest.TestCase):
    def profile(self, bars, **kwargs):
        return engine.volume_profile(engine.normalize(bars), NOW, engine.normalize([]),
                                     **kwargs)

    def test_baseline_compares_same_completed_clock_slots(self):
        bars = [bar for day in weekdays(10)
                for bar in session(day, [10] * 9 + [1_000_000] * 66)]
        bars += session(NOW, [20] * 9 + [9_000_000])
        result = self.profile(bars)
        self.assertEqual(result["expected"], 90)
        self.assertEqual(result["observed"], 180)
        self.assertEqual(result["cutoff"], NOW.replace(minute=0, second=0))

    def test_zero_volume_slot_is_present_and_included(self):
        bars = [bar for day in weekdays(10)
                for bar in session(day, [0] + [10] * 8)]
        bars += session(NOW, [0] * 9)
        result = self.profile(bars)
        self.assertEqual(result["sessions"], 10)
        self.assertEqual(result["expected"], 80)
        self.assertEqual(result["observed"], 0)

    def test_missing_prior_slot_rejects_session_instead_of_compacting(self):
        days = weekdays(10)
        bars = [bar for day in days for i, bar in enumerate(session(day, [10] * 10))
                if not (day == days[0] and i == 4)]
        result = self.profile(bars + session(NOW, [20] * 9))
        self.assertEqual(result["sessions"], 9)
        self.assertIsNone(result["expected"])

    def test_missing_current_slot_returns_unknown_observed_volume(self):
        bars = [bar for day in weekdays(10) for bar in session(day, [10] * 9)]
        current = session(NOW, [20] * 10)
        current.pop(4)
        result = self.profile(bars + current)
        self.assertEqual(result["expected"], 90)
        self.assertIsNone(result["observed"])

    def test_opening_partial_slot_has_no_cumulative_baseline(self):
        now = NOW.replace(hour=9, minute=19)
        result = engine.volume_profile(history()["intraday"], now, engine.normalize([]))
        self.assertIsNone(result["expected"])
        self.assertIsNone(result["observed"])

    def test_baseline_uses_latest_twenty_complete_sessions(self):
        bars = [bar for i, day in enumerate(weekdays(25), 1)
                for bar in session(day, [i] * 9)]
        result = self.profile(bars + session(NOW, [30] * 9))
        self.assertEqual(result["sessions"], 20)
        self.assertEqual(result["expected"], median([9 * i for i in range(6, 26)]))

    def test_regular_baseline_excludes_current_day_and_requires_coverage(self):
        daily = engine.normalize([candle(day, volume=100) for day in weekdays(9)]
                                 + [candle(NOW, volume=9_000_000)])
        result = engine.volume_profile(engine.normalize([]), NOW, daily, "regular")
        self.assertEqual(result["sessions"], 9)
        self.assertIsNone(result["expected"])


class LiquidityTests(unittest.TestCase):
    def test_turnover_uses_traded_average_not_last_price(self):
        result = engine.liquidity(quote(ltp=900), True, SETTINGS)
        self.assertEqual(result["turnover"], 100_000_000)
        self.assertTrue(result["liquidity_eligible"])
        self.assertAlmostEqual(result["spread_bps"], .04 / 101 * 10_000)

    def test_crossed_book_rejected_even_with_high_traded_value(self):
        tick = quote(depth={"buy": [{"price": 102, "quantity": 100_000}],
                            "sell": [{"price": 101, "quantity": 100_000}]})
        result = engine.liquidity(tick, True, SETTINGS)
        self.assertEqual(result["turnover"], 100_000_000)
        self.assertFalse(result["liquidity_eligible"])
        self.assertIsNone(result["spread_bps"])

    def test_missing_or_one_sided_depth_rejected(self):
        for depth in ({}, {"buy": quote()["depth"]["buy"]},
                      {"sell": quote()["depth"]["sell"]}):
            with self.subTest(depth_sides=list(depth)):
                result = engine.liquidity(quote(depth=depth), True, SETTINGS)
                self.assertEqual(result["turnover"], 100_000_000)
                self.assertFalse(result["liquidity_eligible"])

    def test_stale_depth_cannot_be_liquidity_eligible(self):
        self.assertFalse(engine.liquidity(quote(), False, SETTINGS)["liquidity_eligible"])

    def test_missing_average_price_does_not_fabricate_traded_value(self):
        result = engine.liquidity(quote(average_price=None), True, SETTINGS)
        self.assertIsNone(result["turnover"])
        self.assertFalse(result["liquidity_eligible"])

    def test_depth_is_limited_to_first_five_levels_per_side(self):
        tick = quote()
        tick["depth"]["buy"].append({"price": 999, "quantity": 9_000_000})
        tick["depth"]["sell"].append({"price": 1, "quantity": 9_000_000})
        result = engine.liquidity(tick, True, SETTINGS)
        self.assertAlmostEqual(result["depth_value"], 101 * 2_000 * 10)
        self.assertTrue(result["liquidity_eligible"])

    def test_low_traded_value_wide_spread_or_thin_side_rejected(self):
        ticks = [quote(volume=1),
                 quote(depth={"buy": [{"price": 99, "quantity": 100_000}],
                              "sell": [{"price": 102, "quantity": 100_000}]}),
                 quote(depth={"buy": [{"price": 100.98, "quantity": 1}],
                              "sell": [{"price": 101.02, "quantity": 100_000}]})]
        for tick in ticks:
            with self.subTest(volume=tick["volume"], depth=tick["depth"]):
                self.assertFalse(engine.liquidity(tick, True, SETTINGS)["liquidity_eligible"])


class QuoteFreshnessTests(unittest.TestCase):
    def test_old_exchange_timestamp_cannot_be_refreshed_by_receipt_time(self):
        tick = quote(source_at=(NOW - timedelta(seconds=21)).isoformat())
        self.assertFalse(engine.quote_fresh(tick, NOW, 20))

    def test_freshness_requires_both_timestamps_within_boundary(self):
        self.assertTrue(engine.quote_fresh(quote(source_at=NOW - timedelta(seconds=20)), NOW, 20))
        for override in ({"source_at": None}, {"received_at": None},
                         {"received_at": NOW - timedelta(seconds=21)},
                         {"source_at": NOW + timedelta(seconds=1)},
                         {"source_at": NOW - timedelta(days=1)}):
            with self.subTest(override=override):
                self.assertFalse(engine.quote_fresh(quote(**override), NOW, 20))

    def test_offhours_and_weekends_cannot_have_fresh_quotes(self):
        for now in (NOW.replace(hour=8), NOW.replace(hour=15, minute=30),
                    NOW - timedelta(days=1)):
            with self.subTest(now=now):
                self.assertFalse(engine.quote_fresh(quote(now), now, 20))


class MarketContextTests(unittest.TestCase):
    def setUp(self):
        self.members = set(server.SECTOR_DEFINITIONS["NIFTY_50"])
        self.rows = [context_row(symbol) for symbol in sorted(self.members)]

    def context(self, rows, tick=None):
        return engine.market_context(rows, quote() if tick is None else tick,
                                     self.members, NOW, SETTINGS)

    def test_configured_nifty_membership_contains_fifty_unique_stocks(self):
        self.assertEqual(len(self.members), 50)

    def test_fixed_membership_denominator_requires_eighty_percent_quotes(self):
        result = self.context(self.rows[:39])
        self.assertEqual(result["coverage"], 39 / 50)
        self.assertEqual(result["regime"], "insufficient_data")
        self.assertIsNone(result["agreement"])
        self.assertEqual(self.context(self.rows[:40])["regime"], "bullish")

    def test_current_feature_coverage_also_requires_eighty_percent(self):
        for row in self.rows[39:]:
            row["vwap"] = None
        result = self.context(self.rows)
        self.assertEqual(result["coverage"], 1)
        self.assertEqual(result["feature_coverage"], 39 / 50)
        self.assertEqual(result["regime"], "insufficient_data")

    def test_nonmembers_never_change_fixed_nifty_context(self):
        outsiders = [context_row("OUTSIDE" + str(i), sector="OUTSIDE" + str(i),
                                 change=-2, direction=-1, emaTrend5=-1)
                     for i in range(10)]
        self.assertEqual(self.context(self.rows), self.context(self.rows + outsiders))

    def test_context_is_stable_under_row_reordering(self):
        self.assertEqual(self.context(self.rows), self.context(list(reversed(self.rows))))

    def test_stale_official_index_prevents_context_confirmation(self):
        result = self.context(self.rows, quote(source_at=NOW - timedelta(seconds=21)))
        self.assertEqual(result["regime"], "insufficient_data")

    def test_context_provenance_is_oldest_constituent_or_official_index(self):
        for constituent_age, index_age in ((5, 15), (18, 10)):
            with self.subTest(constituent_age=constituent_age, index_age=index_age):
                constituent_at = NOW - timedelta(seconds=constituent_age)
                index_at = NOW - timedelta(seconds=index_age)
                self.rows[0]["quote_as_of"] = constituent_at.isoformat()
                result = self.context(self.rows, quote(source_at=index_at))
                self.assertEqual(result["regime"], "bullish")
                self.assertEqual(result["as_of"], min(constituent_at, index_at).isoformat())
                self.assertEqual(result["session_date"], NOW.date().isoformat())


class SignalTests(unittest.TestCase):
    def attach(self, now, row):
        engine.attach_boosters([row], {"regime": "bullish" if row["direction"] > 0
                                     else "bearish"}, now, SETTINGS)
        return row["booster"]

    def test_confirmed_closed_bar_breakout_is_eligible_long_and_short(self):
        for side in (1, -1):
            with self.subTest(side=side):
                now, row = signal_row(side)
                signal = self.attach(now, row)
                self.assertEqual(signal["status"], "eligible")
                self.assertEqual(signal["side"], "long" if side > 0 else "short")
                self.assertAlmostEqual((signal["target"] - signal["trigger"]) * side,
                                       (signal["trigger"] - signal["stop"]) * side * 1.5)

    def test_signal_expires_at_exact_ttl_boundary(self):
        now, row = signal_row()
        signal = self.attach(now, row)
        expiry = engine.timestamp(signal["expires_at"])
        self.assertEqual((expiry - engine.timestamp(signal["signal_at"])).total_seconds(), 90)
        self.assertEqual(self.attach(expiry, row)["status"], "invalidated")

    def test_reversed_and_overextended_prices_invalidate_signal(self):
        for price in (99.99, 100.11):
            with self.subTest(price=price):
                now, row = signal_row()
                row["ltp"] = price
                self.assertEqual(self.attach(now, row)["status"], "invalidated")

    def test_missing_candle_coverage_cannot_create_eligible_signal(self):
        now, row = signal_row()
        row["_session"] = row["_session"].drop(index=1)
        self.assertNotEqual(self.attach(now, row)["status"], "eligible")

    def test_stale_unready_illiquid_and_wrong_regime_setups_not_eligible(self):
        for field in ("fresh", "indicators_ready", "liquidity_eligible", "_current_features"):
            with self.subTest(field=field):
                now, row = signal_row()
                row[field] = False
                self.assertNotEqual(self.attach(now, row)["status"], "eligible")
        now, row = signal_row()
        engine.attach_boosters([row], {"regime": "bearish"}, now, SETTINGS)
        self.assertNotEqual(row["booster"]["status"], "eligible")


class SerializationTests(unittest.TestCase):
    def test_custom_basket_never_invents_official_index_price(self):
        row = context_row("SYNTH", session_date=NOW.date().isoformat())
        basket = engine.basket_rows([row], {"TEST INDEX": ["SYNTH"]})[0]
        self.assertIsNone(basket["ltp"])
        self.assertFalse(basket["fresh"])
        self.assertFalse(basket["liquidity_eligible"])
        self.assertIn('"ltp": null', json.dumps(basket, allow_nan=False))

    def test_public_row_drops_private_frames_and_replaces_nonfinite_values(self):
        result = engine.public_row({"ltp": math.nan, "score": math.inf,
                                    "_session": object(), "symbol": "SYNTH"})
        self.assertNotIn("_session", result)
        self.assertIsNone(result["ltp"], "Non-finite prices must serialize as null, not NaN")
        self.assertIsNone(result["score"])
        json.dumps(result, allow_nan=False)
