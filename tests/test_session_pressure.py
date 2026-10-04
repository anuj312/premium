"""Cumulative pressure regressions using synthetic bars and guarded server I/O."""

import copy
import json
from datetime import timedelta
from unittest.mock import patch

from fixtures import SETTINGS, ServerCase, candle, engine, server
from test_imbalance import PRESSURE_NOW, pressure_history, pressure_quote, pressure_rows

SESSION_FIELDS = {
    "start", "end", "bars", "volume", "estimated_buy_volume", "estimated_sell_volume",
    "estimated_net_volume", "estimated_imbalance_pct", "previous_imbalance_pct",
    "change_pp", "building", "source", "quality", "method", "actual_buy_volume",
    "actual_sell_volume", "actual_imbalance_pct",
}
CHECK_KEYS = ("depth", "session", "volume", "vwap", "momentum", "breakout", "consecutive", "sector")
WEIGHTS = (20, 15, 15, 10, 10, 10, 10, 10)


def cumulative_history(percentages, volumes, now=PRESSURE_NOW):
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    bars = [dict(candle(start + timedelta(minutes=5 * i), close=99 + (pct + 100) / 100,
                        opening=100, high=101, low=99, volume=volume), source="broker_history")
            for i, (pct, volume) in enumerate(zip(percentages, volumes))]
    return {"intraday": engine.normalize(bars), "regular": engine.normalize([])}


class SessionCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.now = PRESSURE_NOW
        self.clock.return_value = self.now
        self.settings = dict(SETTINGS)
        self.record_alerts = self.enterContext(patch.object(server, "_record_imbalance_alerts"))
        for name in ("_load_history_cache", "_save_history_cache"):
            getattr(server, name).side_effect = AssertionError("Real history cache is forbidden")
        for name in ("pathlib.Path.open", "pathlib.Path.read_text", "pathlib.Path.read_bytes",
                     "live_scanner_server.gzip.open"):
            self.enterContext(patch(name, side_effect=AssertionError("Real disk I/O is forbidden")))

    def features(self, dataset=None, now=None):
        dataset = pressure_history() if dataset is None else dataset
        return engine.imbalance_features(dataset["intraday"], self.now if now is None else now,
                                         self.settings, allow_early=True)

    def attach(self, rows, membership, now=None):
        engine.attach_imbalance(rows, self.now if now is None else now, self.settings,
                                membership, horizon="session")
        return rows[0]["session_pressure"]

    def candidate(self, side=1, **kwargs):
        rows, membership = pressure_rows(side, settings=self.settings, **kwargs)
        self.attach(rows, membership, kwargs.get("now", self.now))
        return rows, membership

    def checks(self, signal):
        return {check["key"]: check["passed"] for check in signal["checks"]}

    def seed_history(self):
        symbols = ["SYNTH", *[f"PEER{i}" for i in range(5)]]
        server.SYMBOL_TO_TOKEN = {symbol: i + 1 for i, symbol in enumerate(symbols)}
        server.TOKEN_TO_SYMBOL = {token: symbol for symbol, token in server.SYMBOL_TO_TOKEN.items()}
        self.enterContext(patch.object(server, "PRIMARY_SECTOR", {symbol: "BANK" for symbol in symbols}))
        dataset = pressure_history()
        for token in server.TOKEN_TO_SYMBOL:
            server.HISTORY[token] = dataset
            server.TICKS[token] = pressure_quote()
        return dataset


class SessionFeatureTests(SessionCase):
    def test_exact_session_contract_and_volume_partition(self):
        feature = self.features()
        value = feature["session"]
        self.assertEqual(set(value), SESSION_FIELDS)
        self.assertEqual(value["start"], self.now.replace(minute=15, second=0).isoformat())
        self.assertEqual(value["end"], self.now.replace(second=0).isoformat())
        self.assertEqual((value["bars"], value["volume"]), (4, 500))
        self.assertAlmostEqual(value["estimated_buy_volume"] + value["estimated_sell_volume"], 500)
        self.assertAlmostEqual(value["estimated_net_volume"],
                               value["estimated_buy_volume"] - value["estimated_sell_volume"])
        self.assertAlmostEqual(value["estimated_imbalance_pct"], value["estimated_net_volume"] / 5)
        self.assertEqual(value["method"], "volume_weighted_close_location")
        for key in ("actual_buy_volume", "actual_sell_volume", "actual_imbalance_pct"):
            self.assertIsNone(value[key])
        json.dumps(value, allow_nan=False)

    def test_volume_weighted_sum_is_not_arithmetic_or_latest_bar_percentage(self):
        now = self.now.replace(minute=30)
        value = self.features(cumulative_history([100, 100, -100], [10, 10, 90]), now)["session"]
        self.assertEqual((value["estimated_buy_volume"], value["estimated_sell_volume"]), (20, 90))
        self.assertAlmostEqual(value["estimated_imbalance_pct"], -100 * 70 / 110)
        self.assertNotAlmostEqual(value["estimated_imbalance_pct"], 100 / 3)
        self.assertNotEqual(value["estimated_imbalance_pct"], -100)
        self.assertEqual(value["previous_imbalance_pct"], 100)
        self.assertAlmostEqual(value["change_pp"], value["estimated_imbalance_pct"] - 100)
        self.assertFalse(value["building"])

    def test_every_current_bar_contributes_not_live_day_volume_or_only_latest(self):
        dataset = pressure_history()
        bars = dataset["intraday"].iloc[-4:].to_dict("records")
        buy = sum(bar["volume"] * (bar["close"] - bar["low"]) / (bar["high"] - bar["low"])
                  for bar in bars)
        rows, _ = self.candidate(dataset=dataset, tick=pressure_quote(volume=99_000_000))
        signal = rows[0]["session_pressure"]
        self.assertAlmostEqual(signal["session"]["estimated_buy_volume"], buy)
        self.assertNotAlmostEqual(signal["session"]["estimated_imbalance_pct"],
                                  signal["candle"]["estimated_imbalance_pct"])
        dataset["intraday"].loc[dataset["intraday"].index[-4], "close"] = 99.4
        changed = self.features(dataset)
        self.assertEqual(changed["candle"], rows[0]["_imbalance_features"]["candle"])
        self.assertLess(changed["session"]["estimated_buy_volume"], buy)

    def test_first_candidate_is_0920_with_unknown_early_features_and_no_alert(self):
        for side in (1, -1):
            for minute in (15, 19, 20, 25):
                with self.subTest(side=side, minute=minute):
                    now = self.now.replace(minute=minute, second=0)
                    rows, membership = self.candidate(side, now=now)
                    row, signal = rows[0], rows[0]["session_pressure"]
                    self.assertIsNone(engine.imbalance_features(
                        engine.complete_bars(pressure_history(side)["intraday"], now), now, self.settings))
                    engine.attach_imbalance(rows, now, self.settings, membership)
                    self.assertIsNone(row["imbalance"])
                    if minute < 20:
                        self.assertIsNone(signal)
                        continue
                    self.assertGreater(signal["session"]["estimated_imbalance_pct"] * side, 20)
                    self.assertEqual(signal["session"]["bars"], (minute - 15) // 5)
                    self.assertIsNone(signal["momentum_pct"])
                    self.assertIsNone(signal["breakout_level"])
                    self.assertIsNone(self.checks(signal)["momentum"])
                    self.assertIsNone(self.checks(signal)["breakout"])
                    self.assertFalse(signal["alert"]["eligible"])
                    self.assertEqual(signal["status"], "watch")
                    if minute == 20:
                        for key in ("previous_imbalance_pct", "change_pp"):
                            self.assertIsNone(signal["session"][key])
                        self.assertFalse(signal["session"]["building"])

    def test_three_bars_form_full_range_but_four_is_earliest_confirmation(self):
        for side in (1, -1):
            rows, _ = self.candidate(side, now=self.now.replace(minute=30))
            signal = rows[0]["session_pressure"]
            self.assertEqual(signal["breakout_level"], 100)
            self.assertIsNotNone(signal["momentum_pct"])
            self.assertIs(self.checks(signal)["breakout"], False)
            self.assertFalse(signal["alert"]["eligible"])
            rows, _ = self.candidate(side)
            self.assertTrue(rows[0]["session_pressure"]["alert"]["eligible"])

    def test_forming_and_future_bars_are_ignored_even_with_unknown_provenance(self):
        dataset = pressure_history()
        before = self.features(dataset)
        end = self.now.replace(second=0)
        extras = [dict(candle(end + timedelta(minutes=i), close=9000, volume=1e9), source="unknown")
                  for i in (0, 5)]
        dataset["intraday"] = engine.normalize(dataset["intraday"].to_dict("records") + extras)
        self.assertEqual(self.features(dataset), before)
        row = self.candidate(dataset=dataset)[0][0]
        self.assertEqual(row["session_pressure"]["session"], before["session"])
        self.assertEqual(self.features(dataset, end - timedelta(microseconds=1))["session"]["bars"], 3)
        self.assertIsNone(engine.imbalance_features(dataset["intraday"], self.now, self.settings))

    def test_every_exact_completed_slot_and_valid_ohlcv_is_required(self):
        for index in range(4):
            for kind in ("missing", "off_grid", "invalid"):
                with self.subTest(index=index, kind=kind):
                    dataset = pressure_history()
                    frame = dataset["intraday"]
                    position = frame.index[-4 + index]
                    if kind == "missing":
                        dataset["intraday"] = frame.drop(index=position)
                    elif kind == "off_grid":
                        frame.loc[position, "date"] += timedelta(seconds=1)
                    else:
                        frame.loc[position, "open"] = 1000
                    self.assertIsNone(self.features(dataset))
                    self.assertIsNone(self.candidate(dataset=dataset)[0][0]["session_pressure"])

    def test_unknown_absent_or_untrusted_source_anywhere_rejects_session(self):
        for index in range(4):
            for source in ("unknown", None, "broker", "absent"):
                with self.subTest(index=index, source=source):
                    dataset = pressure_history()
                    frame = dataset["intraday"]
                    if source == "absent":
                        dataset["intraday"] = frame.drop(columns="source")
                    else:
                        frame.loc[frame.index[-4 + index], "source"] = source
                        dataset["intraday"] = engine.normalize(frame)
                    self.assertIsNone(self.features(dataset))

    def test_provenance_summarizes_all_bars_including_older_sampled_bars(self):
        for indices, source in (([], "broker_history"), ([-4], "mixed"), ([-1], "mixed"),
                                ([-4, -3, -2, -1], "sampled_ticks")):
            with self.subTest(source=source, indices=indices):
                dataset = pressure_history()
                frame = dataset["intraday"]
                frame.loc[[frame.index[i] for i in indices], "source"] = "sampled_ticks"
                signal = self.candidate(dataset=dataset)[0][0]["session_pressure"]
                self.assertEqual(signal["session"]["source"], source)
                self.assertEqual(signal["session"]["quality"],
                                 "broker_confirmed" if not indices else "sampled")
                self.assertEqual(signal["status"], "provisional" if indices else "confirmed")
                self.assertEqual(signal["score"], 100)
                self.assertEqual(signal["alert"]["eligible"], not indices)

    def test_building_cooling_unchanged_neutral_and_side_reversal(self):
        now = self.now.replace(minute=25)
        for prior, latest, expected in ((20, 80, True), (80, 20, False), (40, 40, False),
                                        (-20, -80, True), (-80, -20, False), (-40, -40, False),
                                        (20, -100, False), (-20, 100, False), (0, 80, True)):
            with self.subTest(prior=prior, latest=latest):
                value = self.features(cumulative_history([prior, latest], [100, 100]), now)["session"]
                self.assertAlmostEqual(value["previous_imbalance_pct"], prior)
                self.assertAlmostEqual(value["estimated_imbalance_pct"], (prior + latest) / 2)
                self.assertAlmostEqual(value["change_pp"], (latest - prior) / 2)
                self.assertEqual(value["building"], expected)

    def test_zero_and_flat_bars_partition_volume_without_invented_pressure(self):
        now = self.now.replace(minute=25)
        dataset = cumulative_history([100, 100], [0, 100])
        value = self.features(dataset, now)["session"]
        self.assertEqual(value["estimated_imbalance_pct"], 100)
        self.assertIsNone(value["previous_imbalance_pct"])
        self.assertIsNone(value["change_pp"])
        self.assertFalse(value["building"])
        dataset["intraday"].loc[1, ["open", "high", "low", "close"]] = 100
        feature = self.features(dataset, now)
        self.assertEqual(feature["session"]["estimated_buy_volume"], 50)
        self.assertEqual(feature["session"]["estimated_sell_volume"], 50)
        self.assertEqual(feature["session"]["estimated_imbalance_pct"], 0)
        self.assertIsNone(self.candidate(dataset=dataset, now=now)[0][0]["session_pressure"])
        dataset["intraday"].loc[:, "volume"] = 0
        self.assertIsNone(self.features(dataset, now))

    def test_zero_latest_bar_retains_prior_pressure_but_cannot_confirm(self):
        dataset = pressure_history()
        dataset["intraday"].loc[dataset["intraday"].index[-1], "volume"] = 0
        signal = self.candidate(dataset=dataset)[0][0]["session_pressure"]
        self.assertEqual(signal["session"]["volume"], 300)
        self.assertAlmostEqual(signal["session"]["estimated_imbalance_pct"],
                               signal["session"]["previous_imbalance_pct"])
        self.assertAlmostEqual(signal["session"]["change_pp"], 0)
        self.assertFalse(signal["session"]["building"])
        self.assertIsNone(signal["candle"]["estimated_imbalance_pct"])
        self.assertIs(self.checks(signal)["volume"], False)
        self.assertFalse(signal["alert"]["eligible"])

    def test_new_day_resets_cumulative_totals_previous_and_cached_signal(self):
        dataset = pressure_history()
        old_rows, _ = self.candidate(dataset=dataset)
        later = self.now.replace(minute=20) + timedelta(days=1)
        self.assertIsNone(self.features(dataset, later))
        current = cumulative_history([-80], [25], later)["intraday"]
        dataset["intraday"] = engine.normalize(dataset["intraday"].to_dict("records") +
                                               current.to_dict("records"))
        value = self.features(dataset, later)["session"]
        self.assertEqual((value["bars"], value["volume"]), (1, 25))
        self.assertAlmostEqual(value["estimated_imbalance_pct"], -80)
        self.assertIsNone(value["previous_imbalance_pct"])
        self.assertIsNone(value["change_pp"])
        self.assertFalse(value["building"])
        refreshed = engine.refresh_row(old_rows[0], pressure_quote(now=later), later, self.settings)
        self.assertIsNone(refreshed["session_pressure"])
        self.assertIsNone(refreshed["imbalance"])
        engine.attach_imbalance([refreshed], later, self.settings, {"SYNTH": "BANK"}, horizon="session")
        self.assertIsNone(refreshed["session_pressure"], "Yesterday's cached feature is not today's session")
        self.assertIsNotNone(old_rows[0]["session_pressure"])

    def test_buy_sell_symmetry_for_totals_prior_changes_and_identical_weights(self):
        buy_rows, _ = self.candidate(1)
        sell_rows, _ = self.candidate(-1)
        buy, sell = buy_rows[0]["session_pressure"], sell_rows[0]["session_pressure"]
        for signal in (buy, sell):
            self.assertEqual(tuple(check["key"] for check in signal["checks"]), CHECK_KEYS)
            self.assertEqual(tuple(check["weight"] for check in signal["checks"]), WEIGHTS)
            self.assertEqual((signal["score"], signal["passed"], signal["total"]), (100, 8, 8))
            self.assertEqual(signal["score_version"], "session-pressure-v1")
            self.assertEqual(signal["status"], "confirmed")
            self.assertTrue(signal["alert"]["eligible"])
            self.assertTrue(signal["alert"]["id"].endswith(":session-pressure-v1"))
        self.assertEqual((buy["side"], sell["side"]), ("buy", "sell"))
        self.assertAlmostEqual(buy["session"]["estimated_buy_volume"], sell["session"]["estimated_sell_volume"])
        for key in ("estimated_net_volume", "estimated_imbalance_pct", "previous_imbalance_pct", "change_pp"):
            self.assertAlmostEqual(buy["session"][key], -sell["session"][key])
        self.assertEqual(buy["session"]["building"], sell["session"]["building"])


class SessionCandidateTests(SessionCase):
    def test_legacy_contract_is_unchanged_and_session_attachment_is_independent(self):
        rows, membership = pressure_rows()
        engine.attach_imbalance(rows, self.now, self.settings, membership)
        legacy = copy.deepcopy(rows[0]["imbalance"])
        signal = self.attach(rows, membership)
        self.assertEqual(rows[0]["imbalance"], legacy)
        self.assertEqual(set(signal), set(legacy) | {"session"})
        self.assertNotIn("session", legacy)
        self.assertNotIn("_session_alignment", signal)
        self.assertEqual(tuple(check["weight"] for check in legacy["checks"]), WEIGHTS)
        for key in ("book", "candle", "sector", "feature_as_of", "expires_at", "as_of"):
            self.assertEqual(signal[key], legacy[key])
        self.assertNotEqual(signal["alert"]["id"], legacy["alert"]["id"])
        before = copy.deepcopy(signal)
        engine.attach_imbalance(rows, self.now, self.settings, membership)
        self.assertEqual(rows[0]["session_pressure"], before)
        json.dumps(engine.public_row(rows[0]), allow_nan=False)

    def test_no_volume_spike_is_watch_not_excluded(self):
        for side in (1, -1):
            dataset = pressure_history(side)
            dataset["intraday"].loc[dataset["intraday"].index[-1], "volume"] = 100
            rows, membership = self.candidate(side, dataset=dataset)
            signal = rows[0]["session_pressure"]
            self.assertEqual(signal["bar_volume_ratio"], 1)
            self.assertIs(self.checks(signal)["volume"], False)
            self.assertEqual(signal["status"], "watch")
            self.assertEqual((signal["score"], signal["passed"]), (85, 7))
            self.assertFalse(signal["alert"]["eligible"])
            engine.attach_imbalance(rows, self.now, self.settings, membership)
            self.assertIsNone(rows[0]["imbalance"])

    def test_missing_or_insufficient_baseline_is_unknown_not_a_hard_gate(self):
        for count in (0, 9, 10):
            dataset = pressure_history(prior_sessions=count)
            signal = self.candidate(dataset=dataset)[0][0]["session_pressure"]
            self.assertEqual(signal["baseline_sessions"], count)
            self.assertIs(self.checks(signal)["volume"], None if count < 10 else True)
            self.assertEqual(signal["status"], "watch" if count < 10 else "confirmed")
        rows, membership = pressure_rows()
        rows[0]["_imbalance_features"]["baseline_sessions"] = 9
        signal = self.attach(rows, membership)
        self.assertIs(self.checks(signal)["volume"], None, "Ratio alone cannot establish a baseline")

    def test_opposite_neutral_and_weak_current_depth_are_optional_watch_confirmations(self):
        for side in (1, -1):
            for book in (-side * .5, 0, side * .1999, side * .2):
                with self.subTest(side=side, book=book):
                    rows, membership = pressure_rows(side)
                    rows[0]["depth_quantity_imbalance"] = book
                    signal = self.attach(rows, membership)
                    self.assertEqual(signal["side"], "buy" if side > 0 else "sell")
                    self.assertEqual(self.checks(signal)["depth"], book * side >= .2)
                    self.assertEqual(signal["status"], "confirmed" if book * side >= .2 else "watch")
        rows, _ = self.candidate(tick=pressure_quote(-1))
        self.assertTrue(rows[0]["depth_valid"])
        self.assertIsNotNone(rows[0]["session_pressure"])
        self.assertEqual(rows[0]["session_pressure"]["side"], "buy")
        self.assertFalse(rows[0]["session_pressure"]["alert"]["eligible"])

    def test_session_threshold_is_inclusive_independent_of_latest_estimate(self):
        rows, membership = pressure_rows()
        pct = rows[0]["_imbalance_features"]["session"]["estimated_imbalance_pct"]
        for threshold in (pct - .001, pct, pct + .001):
            self.settings["imbalance_min_pct"] = threshold
            self.assertEqual(self.attach(rows, membership) is not None, threshold <= pct)
        for pct in (0, 19.999, 20):
            rows, membership = pressure_rows()
            rows[0]["_imbalance_features"]["session"]["estimated_imbalance_pct"] = pct
            self.settings = dict(SETTINGS)
            self.assertEqual(self.attach(rows, membership) is not None, pct == 20)

    def test_fresh_valid_depth_and_eligible_liquidity_remain_hard_gates(self):
        for override in ({"fresh": False}, {"liquidity_eligible": False}, {"depth_valid": False},
                         {"depth_quantity_imbalance": None}, {"isIndex": True},
                         {"feature_mode": "regular"}, {"_imbalance_features": None}):
            rows, membership = pressure_rows()
            rows[0].update(override, session_pressure={"status": "confirmed"},
                           imbalance={"keep": True}, building={"keep": True}, booster={"keep": True})
            self.assertIsNone(self.attach(rows, membership))
            for key in ("imbalance", "building", "booster"):
                self.assertEqual(rows[0][key], {"keep": True})
        good = pressure_quote()["depth"]
        for depth in ({}, {"buy": good["buy"]}, {"sell": good["sell"]},
                      {"buy": [{"price": 102, "quantity": 3000}], "sell": good["sell"]},
                      {"buy": [{"price": 0, "quantity": 3000}], "sell": good["sell"]}):
            self.assertIsNone(self.candidate(tick=pressure_quote(depth=depth))[0][0]["session_pressure"])

    def test_depth_and_quote_source_receipt_are_rechecked_independently(self):
        for key in ("depth_as_of", "depth_received_at", "quote_as_of", "quote_received_at"):
            for at in (None, "invalid", self.now - timedelta(seconds=21),
                       self.now + timedelta(seconds=1), self.now - timedelta(days=1)):
                with self.subTest(key=key, at=at):
                    rows, membership = pressure_rows()
                    rows[0][key] = at.isoformat() if hasattr(at, "isoformat") else at
                    self.assertIsNone(self.attach(rows, membership))

    def test_opposite_latest_estimate_does_not_flip_session_or_confirm_recency(self):
        for side in (1, -1):
            dataset = pressure_history(side)
            frame = dataset["intraday"]
            frame.loc[frame.index[-4:-1], "volume"] = 500
            frame.loc[frame.index[-1], "high" if side > 0 else "low"] = 101.2 if side > 0 else 98.8
            rows, _ = self.candidate(side, dataset=dataset)
            signal = rows[0]["session_pressure"]
            self.assertLess(signal["candle"]["estimated_imbalance_pct"] * side, -20)
            self.assertGreater(signal["session"]["estimated_imbalance_pct"] * side, 20)
            self.assertEqual(signal["side"], "buy" if side > 0 else "sell")
            self.assertEqual(signal["consecutive"], 2)
            self.assertEqual(signal["breakout_level"], 100)
            self.assertTrue(signal["breakout"])
            self.assertTrue(all(self.checks(signal).values()))
            self.assertEqual(signal["score"], 100)
            self.assertEqual(signal["status"], "watch")
            self.assertFalse(signal["alert"]["eligible"], "Latest candle must independently align for an alert")

    def test_opposite_latest_body_breaks_consecutive_cumulative_side(self):
        for side in (1, -1):
            dataset = pressure_history(side)
            frame = dataset["intraday"]
            frame.loc[frame.index[-1], "open"] = 100 + side * .35
            signal = self.candidate(side, dataset=dataset)[0][0]["session_pressure"]
            self.assertEqual(signal["consecutive"], 0)
            self.assertIs(self.checks(signal)["consecutive"], False)
            self.assertEqual(signal["side"], "buy" if side > 0 else "sell")
            self.assertFalse(signal["alert"]["eligible"])

    def test_latest_candle_recency_threshold_is_inclusive_and_not_a_candidate_gate(self):
        for side in (1, -1):
            for percentage in (None, -20, 0, 19.999, 20):
                rows, membership = pressure_rows(side)
                rows[0]["_imbalance_features"]["candle"]["estimated_imbalance_pct"] = (
                    percentage * side if percentage is not None else None)
                signal = self.attach(rows, membership)
                self.assertEqual(signal["score"], 100)
                self.assertEqual(signal["alert"]["eligible"], percentage == 20)
                self.assertEqual(signal["status"], "confirmed" if percentage == 20 else "watch")

    def test_optional_vwap_momentum_and_sector_are_relative_to_cumulative_side(self):
        for side in (1, -1):
            for field, check in (("closed_vwap_gap_pct", "vwap"), ("momentum_pct", "momentum"),
                                 ("vwap_slope_pct", "momentum")):
                for value in (0, -side * .1, side * .1):
                    rows, membership = pressure_rows(side)
                    rows[0]["direction"] = -side
                    rows[0]["_imbalance_features"]["side"] = -side
                    rows[0]["_imbalance_features"][field] = value
                    signal = self.attach(rows, membership)
                    self.assertEqual(self.checks(signal)[check], value * side > 0)
                    self.assertIs(self.checks(signal)["sector"], True)
                    self.assertEqual(signal["alert"]["eligible"], value * side > 0)

    def test_new_strict_opening_range_cross_required_not_wick_or_repeat(self):
        for side in (1, -1):
            for close in (100, 100 - side * .01):
                dataset = pressure_history(side)
                frame = dataset["intraday"]
                frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = (
                    [99.85, 100.1, 99.7, close] if side > 0 else [100.15, 100.3, 99.9, close])
                signal = self.candidate(side, dataset=dataset)[0][0]["session_pressure"]
                self.assertIs(self.checks(signal)["breakout"], False)
                self.assertFalse(signal["alert"]["eligible"])
            dataset = pressure_history(side)
            latest = dict(dataset["intraday"].iloc[-1])
            latest.update(date=self.now.replace(second=0), open=100 + side * .3, close=100 + side * .35)
            dataset["intraday"] = engine.normalize(dataset["intraday"].to_dict("records") + [latest])
            signal = self.candidate(side, dataset=dataset, now=self.now + timedelta(minutes=5))[0][0]["session_pressure"]
            self.assertFalse(signal["breakout"])
            self.assertFalse(signal["alert"]["eligible"])


class SessionFreshnessTests(SessionCase):
    def test_independent_sector_uses_full_configured_membership_not_observed_rows(self):
        for side in (1, -1):
            for count in (0, 3, 4, 5):
                rows, membership = pressure_rows(side)
                rows[0]["change"] = -side * 1000
                signal = self.attach(rows[:count + 1], membership)
                self.assertEqual(signal["sector"]["definition"], "excludes_subject")
                self.assertEqual(signal["sector"]["total_peers"], 5)
                self.assertEqual(signal["sector"]["coverage"], count / 5)
                self.assertIs(self.checks(signal)["sector"], True if count >= 4 else None)
                self.assertEqual(signal["sector"]["mean"], side * .5 if count >= 4 else None)
            membership["MISSING_CONFIGURED_PEER"] = "BANK"
            signal = self.attach(rows, membership)
            self.assertEqual(signal["sector"]["total_peers"], 6)
            membership["ANOTHER_MISSING_PEER"] = "BANK"
            signal = self.attach(rows, membership)
            self.assertEqual(signal["sector"]["coverage"], 5 / 7)
            self.assertIsNone(self.checks(signal)["sector"])
            self.assertFalse(signal["alert"]["eligible"])

    def test_stale_unknown_or_opposite_sector_is_optional_not_a_candidate_gate(self):
        for key in ("quote_as_of", "quote_received_at"):
            rows, membership = pressure_rows()
            for peer in rows[1:3]:
                peer[key] = (self.now - timedelta(seconds=21)).isoformat()
            signal = self.attach(rows, membership)
            self.assertEqual(signal["sector"]["coverage"], .6)
            self.assertIsNone(self.checks(signal)["sector"])
            self.assertEqual(signal["score"], 90)
        for side in (1, -1):
            rows, membership = pressure_rows(side)
            for peer in rows[1:]:
                peer["change"] = -side * .5
            signal = self.attach(rows, membership)
            self.assertIs(self.checks(signal)["sector"], False)
            self.assertEqual(signal["status"], "watch")

    def test_known_peer_source_and_receipt_deadlines_expire_entire_snapshot(self):
        for key in ("quote_as_of", "quote_received_at"):
            rows, membership = pressure_rows()
            rows[1][key] = (self.now - timedelta(seconds=19)).isoformat()
            signal = self.attach(rows, membership)
            deadline = self.now + timedelta(seconds=1)
            self.assertEqual(engine.timestamp(signal["expires_at"]), deadline)
            self.assertEqual(signal["sector"]["as_of"], rows[1][key])
            self.assertTrue(engine.imbalance_active(rows[0], deadline - timedelta(microseconds=1),
                                                   self.settings, signal_key="session_pressure"))
            self.assertFalse(engine.imbalance_active(rows[0], deadline, self.settings,
                                                    signal_key="session_pressure"))

    def test_snapshot_and_alert_deadlines_match_legacy_freshness_weights(self):
        for quote_stale, cache_stale in ((20, 30), (600, 7), (600, 600)):
            self.settings.update(quote_stale_sec=quote_stale, cache_stale_sec=cache_stale)
            rows, membership = pressure_rows()
            engine.attach_imbalance(rows, self.now, self.settings, membership)
            signal = self.attach(rows, membership)
            legacy = rows[0]["imbalance"]
            for key in ("expires_at", "as_of", "feature_as_of"):
                self.assertEqual(signal[key], legacy[key])
            self.assertEqual(signal["alert"]["expires_at"], legacy["alert"]["expires_at"])
            self.assertEqual(tuple(check["weight"] for check in signal["checks"]), WEIGHTS)
            self.assertEqual(tuple(check["weight"] for check in legacy["checks"]), WEIGHTS)

    def test_active_rechecks_quotes_feature_session_feed_computation_and_expiry(self):
        rows, _ = self.candidate()
        baseline = rows[0]
        self.assertTrue(engine.imbalance_active(baseline, self.now, self.settings, signal_key="session_pressure"))
        cases = [("row", key, value) for key in ("quote_as_of", "quote_received_at")
                 for value in (None, "invalid", (self.now - timedelta(seconds=21)).isoformat(),
                               (self.now + timedelta(seconds=1)).isoformat(),
                               (self.now - timedelta(days=1)).isoformat())]
        cases += [("row", "session_date", "2026-09-25"), ("row", "fresh", False),
                  ("row", "isIndex", True), ("row", "feature_mode", "regular"),
                  ("signal", "status", "invalidated")]
        cases += [("signal", key, value) for key in ("as_of", "feature_as_of", "expires_at")
                  for value in (None, "invalid", (self.now - timedelta(days=1)).isoformat())]
        cases += [("signal", "as_of", (self.now - timedelta(seconds=31)).isoformat()),
                  ("signal", "as_of", (self.now + timedelta(seconds=1)).isoformat()),
                  ("signal", "expires_at", self.now.isoformat())]
        for target, key, value in cases:
            with self.subTest(target=target, key=key, value=value):
                row = copy.deepcopy(baseline)
                (row if target == "row" else row["session_pressure"])[key] = value
                self.assertFalse(engine.imbalance_active(row, self.now, self.settings,
                                                        signal_key="session_pressure"))
        self.assertFalse(engine.imbalance_active(baseline, self.now, self.settings, live=False,
                                                signal_key="session_pressure"))
        self.assertFalse(engine.imbalance_active(baseline, self.now, self.settings),
                         "Default signal_key still means legacy imbalance")

    def test_alert_ttl_is_separate_from_snapshot_ttl_and_price_must_remain_beyond_trigger(self):
        self.settings.update(quote_stale_sec=180, cache_stale_sec=180)
        for side in (1, -1):
            rows, _ = self.candidate(side)
            baseline = copy.deepcopy(rows[0])
            row, signal = rows[0], rows[0]["session_pressure"]
            alert_end = self.now.replace(second=0) + timedelta(seconds=90)
            self.assertEqual(engine.timestamp(signal["alert"]["expires_at"]), alert_end)
            self.assertLess(alert_end, engine.timestamp(signal["expires_at"]))
            self.assertTrue(engine.imbalance_active(row, alert_end - timedelta(microseconds=1),
                                                   self.settings, signal_key="session_pressure"))
            self.assertFalse(engine.imbalance_active(row, alert_end, self.settings,
                                                    signal_key="session_pressure"))
            for price in (signal["breakout_level"], signal["breakout_level"] - side * .01):
                row = copy.deepcopy(baseline)
                rows[0] = row
                row["ltp"] = price
                self.assertFalse(engine.imbalance_active(row, self.now, self.settings,
                                                        signal_key="session_pressure"))
                self.attach(rows, {child["symbol"]: "BANK" for child in rows})
                self.assertEqual(row["session_pressure"]["status"], "watch")
                self.assertFalse(row["session_pressure"]["alert"]["eligible"])

    def test_cutoff_new_day_and_closed_market_reject_even_long_lived_snapshot(self):
        self.settings.update(quote_stale_sec=600, cache_stale_sec=600)
        rows, _ = self.candidate()
        row = rows[0]
        for later in (self.now.replace(minute=40, second=0), self.now + timedelta(days=1),
                      self.now.replace(hour=15, minute=30)):
            self.assertFalse(engine.imbalance_active(row, later, self.settings,
                                                    signal_key="session_pressure"))


class SessionCacheTests(SessionCase):
    def test_server_attaches_session_after_legacy_and_regular_rows_initialize_none(self):
        self.seed_history()
        with patch.object(server, "attach_imbalance", wraps=engine.attach_imbalance) as attach:
            server._refresh_scan_cache(self.now)
        self.assertEqual(attach.call_count, 2)
        self.assertEqual(attach.call_args_list[0].kwargs, {})
        self.assertEqual(attach.call_args_list[1].kwargs, {"horizon": "session"})
        self.assertTrue(all(row["session_pressure"]["status"] == "confirmed"
                            for row in server.CACHE["intraday"]))
        self.assertTrue(all(row["session_pressure"] is None for row in server.CACHE["regular"]))
        self.record_alerts.assert_called_once()

    def test_warm_refresh_reuses_single_feature_calculation_and_stable_session_id(self):
        self.seed_history()
        with patch.object(server, "build_row", wraps=engine.build_row) as builder, \
                patch.object(engine, "imbalance_features", wraps=engine.imbalance_features) as features:
            server._refresh_scan_cache(self.now)
            self.assertEqual((builder.call_count, features.call_count), (12, 6))
            before = copy.deepcopy(server.CACHE["intraday"][0]["session_pressure"])
            later = self.now + timedelta(seconds=1)
            self.clock.return_value = later
            for token in server.TICKS:
                server.TICKS[token] = pressure_quote(now=later, ltp=100.35)
            server._refresh_scan_cache(later)
            self.assertEqual((builder.call_count, features.call_count), (12, 6))
        after = server.CACHE["intraday"][0]["session_pressure"]
        self.assertEqual(after["session"], before["session"])
        self.assertEqual(after["alert"]["id"], before["alert"]["id"])
        self.assertEqual(after["book"]["received_at"], later.isoformat())
        self.assertEqual(after["as_of"], later.isoformat())

    def test_warm_opposite_depth_removes_legacy_but_keeps_session_watch(self):
        self.seed_history()
        server._refresh_scan_cache(self.now)
        for token in server.TICKS:
            server.TICKS[token] = pressure_quote(-1)
        server._refresh_scan_cache(self.now)
        for row in server.CACHE["intraday"]:
            self.assertIsNone(row["imbalance"])
            self.assertEqual(row["session_pressure"]["status"], "watch")
            self.assertEqual(row["session_pressure"]["side"], "buy")
            self.assertFalse(row["session_pressure"]["alert"]["eligible"])

    def test_older_sampled_bar_backfill_rebuilds_all_session_provenance(self):
        dataset = self.seed_history()
        frame = dataset["intraday"].copy()
        frame.loc[frame.index[-4], "source"] = "sampled_ticks"
        server.HISTORY[1] = dict(dataset, intraday=frame)
        server._refresh_scan_cache(self.now)
        signal = next(row for row in server.CACHE["intraday"] if row["symbol"] == "SYNTH")["session_pressure"]
        self.assertEqual(signal["session"]["source"], "mixed")
        self.assertEqual(signal["status"], "provisional")
        self.assertFalse(signal["alert"]["eligible"])
        server.HISTORY[1] = dataset
        server._refresh_scan_cache(self.now)
        signal = next(row for row in server.CACHE["intraday"] if row["symbol"] == "SYNTH")["session_pressure"]
        self.assertEqual(signal["status"], "confirmed")
        self.assertTrue(signal["alert"]["eligible"])

    def test_publication_independently_invalidates_session_without_duplicate_logging(self):
        self.seed_history()
        published = self.now + timedelta(seconds=2)
        self.clock.return_value = published

        def attach(rows, now, settings, membership, horizon="candle"):
            engine.attach_imbalance(rows, now, settings, membership, horizon=horizon)
            if horizon == "session":
                for row in rows:
                    row["session_pressure"]["expires_at"] = (now + timedelta(seconds=1)).isoformat()

        with patch.object(server, "attach_imbalance", side_effect=attach):
            server._refresh_scan_cache(self.now)
        for row in server.CACHE["intraday"]:
            self.assertEqual(row["imbalance"]["status"], "confirmed")
            self.assertTrue(row["imbalance"]["alert"]["eligible"])
            self.assertEqual(row["session_pressure"]["status"], "invalidated")
            self.assertFalse(row["session_pressure"]["alert"]["eligible"])
        self.record_alerts.assert_called_once()
        self.assertEqual(self.record_alerts.call_args.args[1], published)

    def test_stale_publication_invalidates_both_before_any_logging(self):
        self.seed_history()
        self.clock.return_value = self.now + timedelta(seconds=21)
        server._refresh_scan_cache(self.now)
        for row in server.CACHE["intraday"]:
            for key in ("imbalance", "session_pressure"):
                self.assertEqual(row[key]["status"], "invalidated")
                self.assertFalse(row[key]["alert"]["eligible"])
        self.record_alerts.assert_not_called()
        self.record_signals.assert_not_called()

    def test_server_sector_denominator_includes_configured_unsubscribed_peers(self):
        self.seed_history()
        server.PRIMARY_SECTOR.update(MISSING0="BANK", MISSING1="BANK")
        server._refresh_scan_cache(self.now)
        for row in server.CACHE["intraday"]:
            signal = row["session_pressure"]
            self.assertEqual(signal["sector"]["total_peers"], 7)
            self.assertEqual(signal["sector"]["coverage"], round(5 / 7, 4))
            self.assertIsNone(self.checks(signal)["sector"])
            self.assertEqual(signal["status"], "watch")


class SessionApiTests(SessionCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.login().status_code, 200)
        rows, membership = self.candidate()
        engine.attach_imbalance(rows, self.now, self.settings, membership)
        self.seed_cache([engine.public_row(rows[0])])
        server.CACHE["updated_at"] = self.now.isoformat()
        server.CACHE["context"].update(as_of=self.now.isoformat(), session_date=self.now.date().isoformat())
        server.TICKS[1] = pressure_quote()

    def test_api_returns_session_and_unchanged_legacy_ui_settings_on_a_deep_copy(self):
        before = copy.deepcopy(server.CACHE)
        with patch.object(server, "jsonify", wraps=server.jsonify) as render:
            payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["rows"][0]["session_pressure"], before["intraday"][0]["session_pressure"])
        self.assertEqual(payload["imbalance_settings"], {
            "experimental": True, "score_version": "pressure-v1", "min_pct": 20,
            "min_bar_rvol": 1.5, "baseline_sessions": 10, "sector_coverage": .8})
        self.assertEqual(self.strict_json(self.client.get("/api/health"))["version"], "premium-2.4-session-pressure")
        served = render.call_args.kwargs["rows"][0]["session_pressure"]
        cached = server.CACHE["intraday"][0]["session_pressure"]
        for key in ("session", "candle", "book", "checks", "sector", "alert"):
            self.assertIsNot(served[key], cached[key])
        served["session"]["estimated_imbalance_pct"] = -100
        served["checks"][0]["passed"] = False
        served["alert"]["eligible"] = False
        self.assertEqual(server.CACHE, before)
        self.record_alerts.assert_not_called()

    def test_api_invalidates_each_signal_independently_without_mutating_cache(self):
        baseline = copy.deepcopy(server.CACHE)
        for expired_key in ("imbalance", "session_pressure"):
            for field, value in (("as_of", (self.now - timedelta(seconds=31)).isoformat()),
                                 ("expires_at", None), ("expires_at", "invalid"),
                                 ("expires_at", self.now.isoformat())):
                with self.subTest(key=expired_key, field=field):
                    server.CACHE = copy.deepcopy(baseline)
                    server.CACHE["intraday"][0][expired_key][field] = value
                    before = copy.deepcopy(server.CACHE)
                    row = self.strict_json(self.scan())["rows"][0]
                    other = "imbalance" if expired_key == "session_pressure" else "session_pressure"
                    self.assertEqual(row[expired_key]["status"], "invalidated")
                    self.assertFalse(row[expired_key]["alert"]["eligible"])
                    self.assertEqual(row[other], before["intraday"][0][other])
                    self.assertEqual(server.CACHE, before)

    def test_api_quote_receipt_cache_and_feed_invalidations_are_nonmutating(self):
        baseline = copy.deepcopy(server.CACHE)
        cases = [("row", key, value) for key in ("quote_as_of", "quote_received_at")
                 for value in (None, "invalid", (self.now - timedelta(seconds=21)).isoformat(),
                               (self.now + timedelta(seconds=1)).isoformat())]
        cases += [("cache", "updated_at", (self.now - timedelta(seconds=31)).isoformat()),
                  ("cache", "updated_at", None), ("feed", "TICKER_CONNECTED", False),
                  ("feed", "SEED_IN_PROGRESS", True)]
        for target, key, value in cases:
            with self.subTest(target=target, key=key, value=value):
                server.CACHE = copy.deepcopy(baseline)
                with patch.object(server, "TICKER_CONNECTED", True), patch.object(server, "SEED_IN_PROGRESS", False):
                    if target == "feed":
                        setattr(server, key, value)
                    else:
                        (server.CACHE if target == "cache" else server.CACHE["intraday"][0])[key] = value
                    before = copy.deepcopy(server.CACHE)
                    signal = self.strict_json(self.scan())["rows"][0]["session_pressure"]
                    self.assertEqual(signal["status"], "invalidated")
                    self.assertFalse(signal["alert"]["eligible"])
                    self.assertEqual(server.CACHE, before)

    def test_regular_and_basket_exclusions_are_explicit_none(self):
        before = copy.deepcopy(server.CACHE)
        regular = self.strict_json(self.scan(type="regular"))["rows"]
        self.assertIsNone(regular[0]["session_pressure"])
        self.assertIsNone(regular[0]["imbalance"])
        with patch.object(server, "INDEX_GROUPS", {"SYNTH BASKET": ["SYNTH"]}):
            baskets = self.strict_json(self.scan(universe="index"))["rows"]
        self.assertEqual(len(baskets), 1)
        self.assertTrue(baskets[0]["isIndex"])
        self.assertIsNone(baskets[0]["session_pressure"])
        self.assertIsNone(baskets[0]["imbalance"])
        self.assertEqual(server.CACHE, before)

    def test_api_new_cutoff_day_and_offhours_cannot_rescue_old_feature(self):
        baseline = copy.deepcopy(server.CACHE)
        for later in (self.now.replace(minute=40, second=0), self.now + timedelta(days=1),
                      self.now.replace(hour=17)):
            server.CACHE = copy.deepcopy(baseline)
            self.clock.return_value = later
            server.CACHE["updated_at"] = later.isoformat()
            server.TICKS[1] = pressure_quote(now=later)
            row = server.CACHE["intraday"][0]
            row.update(quote_as_of=later.isoformat(), quote_received_at=later.isoformat())
            row["session_pressure"].update(as_of=later.isoformat(),
                                            expires_at=(later + timedelta(seconds=20)).isoformat())
            before = copy.deepcopy(server.CACHE)
            signal = self.strict_json(self.scan())["rows"][0]["session_pressure"]
            self.assertEqual(signal["status"], "invalidated")
            self.assertFalse(signal["alert"]["eligible"])
            self.assertEqual(server.CACHE, before)

    def test_api_rechecks_separate_alert_ttl_and_price_reversal_independently(self):
        baseline = copy.deepcopy(server.CACHE)
        for kind in ("ttl", "equal", "reversed"):
            server.CACHE = copy.deepcopy(baseline)
            signal = server.CACHE["intraday"][0]["session_pressure"]
            if kind == "ttl":
                signal["alert"]["expires_at"] = self.now.isoformat()
            else:
                signal["breakout_level"] = server.CACHE["intraday"][0]["ltp"] + (0 if kind == "equal" else .01)
            before = copy.deepcopy(server.CACHE)
            row = self.strict_json(self.scan())["rows"][0]
            self.assertEqual(row["session_pressure"]["status"], "invalidated")
            self.assertFalse(row["session_pressure"]["alert"]["eligible"])
            self.assertTrue(row["imbalance"]["alert"]["eligible"])
            self.assertEqual(server.CACHE, before)

    def test_api_alert_deadline_expires_before_live_snapshot_deadline(self):
        self.settings.update(quote_stale_sec=180, cache_stale_sec=180)
        server.SETTINGS.update(self.settings)
        rows, _ = self.candidate()
        server.CACHE["intraday"] = [engine.public_row(rows[0])]
        signal = server.CACHE["intraday"][0]["session_pressure"]
        self.clock.return_value = engine.timestamp(signal["alert"]["expires_at"])
        self.assertLess(self.clock.return_value, engine.timestamp(signal["expires_at"]))
        before = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["rows"][0]["session_pressure"]["status"], "invalidated")
        self.assertFalse(payload["rows"][0]["session_pressure"]["alert"]["eligible"])
        self.assertEqual(server.CACHE, before)

    def test_sector_filter_never_rescores_session_against_filtered_peers(self):
        rows, _ = self.candidate()
        server.CACHE["intraday"] = [engine.public_row(row) for row in rows]
        expected = copy.deepcopy(server.CACHE["intraday"][0]["session_pressure"])
        with patch.object(server, "SECTOR_DEFINITIONS", {"BANK": ["SYNTH"]}), \
                patch.object(server, "attach_imbalance", side_effect=AssertionError("API must not rescore")):
            all_rows = self.strict_json(self.scan())["rows"]
            filtered = self.strict_json(self.scan(sector="BANK"))["rows"]
        self.assertEqual(len(all_rows), 6)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["session_pressure"], expected)
        self.assertEqual(expected["sector"]["total_peers"], 5)
        self.assertEqual(expected["sector"]["mean"], .5)
