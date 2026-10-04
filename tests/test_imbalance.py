"""Synthetic pressure regressions; no credentials, broker, threads or real files.

imbalance_features receives completed normalized bars, as build_row supplies them.
SETTINGS deliberately omits new thresholds to exercise the engine's get defaults.
"""

import copy
import io
import json
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures import (NOW, SETTINGS, ServerCase, candle, context_row, engine,
                      quote, server, session, weekdays)

PRESSURE_NOW = NOW.replace(hour=9, minute=35, second=5, microsecond=0)
CHECK_KEYS = ("depth", "candle", "volume", "vwap", "momentum", "breakout",
              "consecutive", "sector")
SAVE_HISTORY_CACHE = server._save_history_cache
LOAD_HISTORY_CACHE = server._load_history_cache
RECORD_IMBALANCE_ALERTS = server._record_imbalance_alerts
RECORD_SIGNALS = server._record_signals
PATH_OPEN = Path.open


def pressure_history(side=1, now=PRESSURE_NOW, prior_sessions=20):
    prior = [dict(bar, source="broker_history")
             for day in weekdays(prior_sessions, now)
             for bar in session(day, [100.0] * 75)]
    current = session(now, [100.0, 100.0, 100.0, 200.0])
    prices = [(99.85, 100, 99.4, 99.8), (99.9, 100, 99.5, 99.85),
              (99.85, 100, 99.7, 99.95), (99.95, 100.4, 99.85, 100.3)]
    for bar, (opening, high, low, close) in zip(current, prices):
        bar.update(open=opening, high=high, low=low, close=close,
                   source="broker_history")
        if side < 0:
            bar.update(open=200 - opening, high=200 - low,
                       low=200 - high, close=200 - close)
    daily = [candle(day.replace(hour=0, minute=0), close=95, volume=7500)
             for day in weekdays(30, now)]
    return {"intraday": engine.normalize(prior + current),
            "regular": engine.normalize(daily)}


def pressure_quote(side=1, now=PRESSURE_NOW, **overrides):
    price = 100 + side * .32
    tick = quote(now, ltp=price, average_price=100 - side * .1,
                 ohlc={"open": 100 - side * .15, "close": 95},
                 depth={"buy": [{"price": price - .02,
                                  "quantity": 3000 if side > 0 else 1000}] * 5,
                        "sell": [{"price": price + .02,
                                   "quantity": 1000 if side > 0 else 3000}] * 5})
    tick.update(overrides)
    return tick


def pressure_rows(side=1, now=PRESSURE_NOW, dataset=None, tick=None, settings=SETTINGS):
    row = engine.build_row("SYNTH", "BANK", pressure_history(side, now) if dataset is None else dataset,
                           pressure_quote(side, now) if tick is None else tick, now, settings)
    peers = [context_row(f"PEER{i}", now=now, change=side * .5,
                         quote_received_at=now.isoformat(), booster=None, building=None,
                         imbalance=None, liquidity_eligible=True, feature_mode="intraday",
                         session_date=now.date().isoformat()) for i in range(5)]
    membership = {child["symbol"]: "BANK" for child in [row, *peers]}
    return [row, *peers], membership


class PressureCase(ServerCase):
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

    def attach(self, rows, membership, now=None):
        engine.attach_imbalance(rows, self.now if now is None else now, self.settings, membership)
        return rows[0]["imbalance"]

    def candidate(self, side=1, **kwargs):
        rows, membership = pressure_rows(side, settings=self.settings, **kwargs)
        self.attach(rows, membership, kwargs.get("now", self.now))
        return rows, membership


    def features(self, dataset=None, now=None):
        now = self.now if now is None else now
        dataset = pressure_history() if dataset is None else dataset
        return engine.imbalance_features(engine.complete_bars(dataset["intraday"], now), now, self.settings)


class PressureFeatureTests(PressureCase):
    def test_buy_and_sell_full_confirmation_has_current_payload_and_true_sources(self):
        for side in (1, -1):
            with self.subTest(side=side):
                rows, _ = self.candidate(side)
                signal = rows[0]["imbalance"]
                self.assertEqual(signal["status"], "confirmed")
                self.assertEqual(signal["side"], "buy" if side > 0 else "sell")
                self.assertEqual((signal["score"], signal["passed"], signal["total"]), (100, 8, 8))
                self.assertEqual(signal["score_version"], "pressure-v1")
                self.assertEqual(tuple(check["key"] for check in signal["checks"]), CHECK_KEYS)
                self.assertEqual(sum(check["weight"] for check in signal["checks"]), 100)
                self.assertTrue(all(check["passed"] is True for check in signal["checks"]))
                self.assertTrue(all(check["label"] and check["detail"] for check in signal["checks"]))
                self.assertEqual(signal["candle"]["source"], "broker_history")
                self.assertEqual(signal["candle"]["quality"], "broker_confirmed")
                self.assertEqual(signal["candle"]["method"], "close_location")
                self.assertEqual(signal["book"]["source"], "broker_depth")
                self.assertEqual(signal["book"]["definition"], "resting_quantity")
                self.assertEqual(signal["vwap_source"], "ohlcv_hlc3")
                self.assertEqual(signal["candle"]["start"], self.now.replace(minute=30, second=0).isoformat())
                self.assertEqual(signal["feature_as_of"], self.now.replace(second=0).isoformat())
                self.assertEqual(signal["consecutive"], 2)
                self.assertEqual(signal["baseline_sessions"], 20)
                self.assertEqual(signal["bar_volume_ratio"], 2)
                self.assertIs(signal["alert"]["eligible"], True)
                self.assertFalse({"entry", "stop", "target", "probability"} & signal.keys())
                for key in ("actual_buy_volume", "actual_sell_volume", "actual_imbalance_pct"):
                    self.assertIsNone(signal["candle"][key])
                json.dumps(engine.public_row(rows[0]), allow_nan=False)

    def test_close_location_estimates_partition_volume_with_signed_percentage(self):
        for side in (1, -1):
            with self.subTest(side=side):
                signal = self.features(pressure_history(side))
                bar = signal["candle"]
                fraction = (bar["close"] - bar["low"]) / (bar["high"] - bar["low"])
                self.assertAlmostEqual(bar["estimated_buy_volume"], 200 * fraction)
                self.assertAlmostEqual(bar["estimated_sell_volume"], 200 * (1 - fraction))
                self.assertAlmostEqual(bar["estimated_imbalance_pct"], (2 * fraction - 1) * 100)
                self.assertAlmostEqual(bar["estimated_imbalance_pct"] * side, 700 / 11)

    def test_sampled_and_mixed_sources_remain_provisional_with_no_alert(self):
        for side in (1, -1):
            for source in ("sampled_ticks", "mixed"):
                with self.subTest(side=side, source=source):
                    dataset = pressure_history(side)
                    frame = dataset["intraday"]
                    indices = frame.index[frame["date"].dt.date == self.now.date()]
                    frame.loc[indices if source == "sampled_ticks" else indices[-1:], "source"] = "sampled_ticks"
                    rows, _ = self.candidate(side, dataset=dataset)
                    signal = rows[0]["imbalance"]
                    self.assertEqual(signal["status"], "provisional")
                    self.assertEqual(signal["score"], 100)
                    self.assertEqual(signal["candle"]["source"], source)
                    self.assertEqual(signal["candle"]["quality"], "sampled")
                    self.assertIs(signal["alert"]["eligible"], False)

    def test_normalizer_preserves_only_recognized_provenance(self):
        at = self.now - timedelta(minutes=5)
        for source in ("broker_history", "sampled_ticks", "mixed", "broker", "unknown", None, 123):
            with self.subTest(source=source):
                frame = engine.normalize([dict(candle(at), source=source)])
                expected = source if source in ("broker_history", "sampled_ticks") else "unknown"
                self.assertEqual(frame.iloc[0]["source"], expected)
        self.assertNotIn("source", engine.normalize([candle(at)]).columns)

    def test_unknown_null_and_absent_current_source_reject_pressure(self):
        for source in ("unknown", None, "untrusted", "absent"):
            with self.subTest(source=source):
                dataset = pressure_history()
                frame = dataset["intraday"]
                if source == "absent":
                    dataset["intraday"] = frame.drop(columns="source")
                else:
                    frame.loc[frame.index[-1], "source"] = source
                    dataset["intraday"] = engine.normalize(frame)
                self.assertIsNone(self.features(dataset))
                rows, _ = self.candidate(dataset=dataset)
                self.assertIsNone(rows[0]["imbalance"])

    def test_same_slot_baseline_accepts_ten_sessions_but_not_nine(self):
        for count in (9, 10, 20):
            with self.subTest(count=count):
                dataset = pressure_history(prior_sessions=count)
                feature = self.features(dataset)
                self.assertEqual(feature["baseline_sessions"], count)
                self.assertEqual(feature["bar_volume_ratio"], None if count == 9 else 2)
                rows, _ = self.candidate(dataset=dataset)
                self.assertEqual(rows[0]["imbalance"] is not None, count >= 10)

    def test_baseline_uses_latest_twenty_valid_broker_same_clock_bars(self):
        dataset = pressure_history(prior_sessions=25)
        frame = dataset["intraday"]
        days = weekdays(25, self.now)
        for index, day in enumerate(days, 1):
            mask = (frame["date"].dt.date == day.date()) & (frame["date"].dt.time == self.now.replace(minute=30, second=0).time())
            frame.loc[mask, "volume"] = index
        feature = self.features(dataset)
        self.assertEqual(feature["baseline_sessions"], 20)
        self.assertAlmostEqual(feature["bar_volume_ratio"], 200 / 15.5)

    def test_invalid_sampled_unknown_and_missing_prior_slots_are_not_counted(self):
        for kind in ("invalid", "sampled", "unknown", "missing"):
            with self.subTest(kind=kind):
                dataset = pressure_history(prior_sessions=10)
                frame = dataset["intraday"]
                position = frame.index[3]
                if kind == "missing":
                    dataset["intraday"] = frame.drop(index=position)
                elif kind == "invalid":
                    frame.loc[position, "open"] = 1000
                else:
                    frame.loc[position, "source"] = "sampled_ticks" if kind == "sampled" else "unknown"
                feature = self.features(dataset)
                self.assertEqual(feature["baseline_sessions"], 9)
                self.assertIsNone(feature["bar_volume_ratio"])

    def test_valid_zero_prior_volumes_count_but_zero_median_is_unknown_rvol(self):
        for zeros, expected in ((5, 4), (10, None)):
            with self.subTest(zeros=zeros):
                dataset = pressure_history(prior_sessions=10)
                frame = dataset["intraday"]
                positions = [i for i in frame.index[:-4] if frame.loc[i, "date"].hour == 9 and
                             frame.loc[i, "date"].minute == 30]
                frame.loc[positions[:zeros], "volume"] = 0
                feature = self.features(dataset)
                self.assertEqual(feature["baseline_sessions"], 10)
                self.assertEqual(feature["bar_volume_ratio"], expected)

    def test_bar_rvol_is_not_cumulative_session_or_broker_day_volume_ratio(self):
        rows, _ = self.candidate(tick=pressure_quote(volume=99_000_000))
        row = rows[0]
        self.assertEqual(row["ratio"], 1.25)
        self.assertEqual(row["timeVolumeRatio"], 1.25)
        self.assertEqual(row["imbalance"]["bar_volume_ratio"], 2)
        self.assertEqual(row["imbalance"]["candle"]["volume"], 200)

    def test_missing_different_prior_clock_slots_do_not_bias_bar_baseline(self):
        dataset = pressure_history(prior_sessions=10)
        frame = dataset["intraday"]
        dataset["intraday"] = frame.drop(index=frame.index[0])
        feature = self.features(dataset)
        self.assertEqual(feature["baseline_sessions"], 10)
        self.assertEqual(feature["bar_volume_ratio"], 2)
        row = engine.build_row("SYNTH", "BANK", dataset, pressure_quote(), self.now, self.settings)
        self.assertEqual(row["volume_baseline_sessions"], 9)
        self.assertIsNone(row["timeVolumeRatio"])


class PressureDepthTests(PressureCase):
    def test_quantity_imbalance_and_notional_imbalance_use_distinct_math(self):
        depth = {"buy": [{"price": 98, "quantity": 15000}],
                 "sell": [{"price": 102, "quantity": 10000}]}
        self.settings["max_spread_bps"] = 1000
        rows, _ = self.candidate(tick=pressure_quote(depth=depth))
        row, book = rows[0], rows[0]["imbalance"]["book"]
        expected_notional = (98 * 15000 - 102 * 10000) / (98 * 15000 + 102 * 10000)
        self.assertTrue(row["depth_valid"])
        self.assertEqual(row["depth_quantity_imbalance"], .2)
        self.assertAlmostEqual(row["book_imbalance"], expected_notional)
        self.assertEqual(book["imbalance_pct"], 20)
        self.assertAlmostEqual(book["notional_imbalance_pct"], expected_notional * 100)
        self.assertLess(book["notional_imbalance_pct"], 20)
        self.assertEqual(rows[0]["imbalance"]["status"], "confirmed")

    def test_depth_uses_at_most_five_valid_levels_with_quantity_totals(self):
        tick = pressure_quote()
        tick["depth"]["buy"].append({"price": 1000, "quantity": 1e9})
        tick["depth"]["sell"].append({"price": 1, "quantity": 1e9})
        result = engine.liquidity(tick, True, self.settings)
        self.assertTrue(result["depth_valid"])
        self.assertEqual((result["depth_bid_levels"], result["depth_ask_levels"]), (5, 5))
        self.assertEqual((result["depth_bid_quantity"], result["depth_ask_quantity"]), (15000, 5000))
        self.assertEqual(result["depth_quantity_imbalance"], .5)
        self.assertEqual(result["depth_as_of"], tick["source_at"])
        self.assertEqual(result["depth_received_at"], tick["received_at"])

    def test_missing_one_sided_crossed_and_malformed_arrays_reject_pressure(self):
        good = pressure_quote()["depth"]
        invalid = [None, [], "bad", {}, {"buy": good["buy"]}, {"sell": good["sell"]},
                   {"buy": (), "sell": good["sell"]},
                   {"buy": good["buy"], "sell": {"price": 101, "quantity": 1000}},
                   {"buy": [{"price": 101, "quantity": 10000}],
                    "sell": [{"price": 100, "quantity": 10000}]}]
        for depth in invalid:
            with self.subTest(depth=depth):
                rows, _ = self.candidate(tick=pressure_quote(depth=depth))
                self.assertFalse(rows[0]["depth_valid"])
                self.assertIsNone(rows[0]["depth_quantity_imbalance"])
                self.assertIsNone(rows[0]["imbalance"])

    def test_bad_level_among_good_depth_rejects_quantity_pressure(self):
        bad_levels = [None, 42, [], {}, {"price": 100, "quantity": None},
                      {"price": float("inf"), "quantity": 1000},
                      {"price": 100, "quantity": float("nan")},
                      {"price": 100, "quantity": -1}, {"price": -1, "quantity": 1000}]
        for side in ("buy", "sell"):
            for level in bad_levels:
                with self.subTest(side=side, level=level):
                    tick = pressure_quote()
                    tick["depth"][side][2] = level
                    result = engine.liquidity(tick, True, self.settings)
                    self.assertFalse(result["depth_valid"])
                    self.assertIsNone(result["depth_quantity_imbalance"])
                    rows, _ = self.candidate(tick=tick)
                    self.assertIsNone(rows[0]["imbalance"])

    def test_zero_quantity_broker_padding_does_not_invent_levels_or_imbalance(self):
        tick = pressure_quote(depth={"buy": [{"price": 100.3, "quantity": 3000},
                                             {"price": 0, "quantity": 0}],
                                     "sell": [{"price": 100.34, "quantity": 1000},
                                              {"price": 0, "quantity": 0}]})
        result = engine.liquidity(tick, True, self.settings)
        self.assertTrue(result["depth_valid"])
        self.assertEqual((result["depth_bid_levels"], result["depth_ask_levels"]), (1, 1))
        self.assertEqual(result["depth_quantity_imbalance"], .5)

    def test_positive_quantity_at_zero_price_is_not_valid_broker_padding(self):
        tick = pressure_quote()
        tick["depth"]["buy"][2] = {"price": 0, "quantity": 5000}
        result = engine.liquidity(tick, True, self.settings)
        self.assertFalse(result["depth_valid"], "A live size without a positive price is malformed")
        self.assertIsNone(result["depth_quantity_imbalance"])

    def test_stale_source_or_receipt_cannot_be_rescued_by_the_other_timestamp(self):
        for key in ("source_at", "received_at"):
            for at in (None, "invalid", self.now - timedelta(seconds=21),
                       self.now + timedelta(seconds=1), self.now - timedelta(days=1)):
                with self.subTest(key=key, at=at):
                    tick = pressure_quote(**{key: at.isoformat() if hasattr(at, "isoformat") else at})
                    rows, _ = self.candidate(tick=tick)
                    self.assertFalse(rows[0]["fresh"])
                    self.assertFalse(rows[0]["depth_valid"])
                    self.assertIsNone(rows[0]["imbalance"])

    def test_attach_rechecks_depth_timestamps_even_if_row_fresh_is_true(self):
        for key in ("depth_as_of", "depth_received_at"):
            for at in (None, self.now - timedelta(seconds=21), self.now + timedelta(seconds=1)):
                with self.subTest(key=key, at=at):
                    rows, membership = pressure_rows()
                    rows[0][key] = at.isoformat() if at is not None else None
                    self.assertIsNone(self.attach(rows, membership))

    def test_default_and_configured_pressure_and_bar_volume_gates_are_inclusive(self):
        self.assertNotIn("imbalance_min_pct", SETTINGS)
        self.assertNotIn("imbalance_min_bar_rvol", SETTINGS)
        for key, below, boundary, rejected in (("imbalance_min_pct", 49.99, 50, 50.01),
                                               ("imbalance_min_bar_rvol", 1.99, 2, 2.01)):
            for value in (below, boundary, rejected):
                with self.subTest(key=key, value=value):
                    self.settings = dict(SETTINGS, **{key: value})
                    rows, _ = self.candidate()
                    self.assertEqual(rows[0]["imbalance"] is not None, value != rejected)

    def test_candle_depth_and_volume_each_must_clear_default_gates(self):
        for field, value in (("estimate", 19.99), ("book", .1999), ("ratio", 1.4999)):
            with self.subTest(field=field):
                rows, membership = pressure_rows()
                feature = rows[0]["_imbalance_features"]
                if field == "estimate":
                    feature["candle"]["estimated_imbalance_pct"] = value
                elif field == "book":
                    rows[0]["depth_quantity_imbalance"] = value
                else:
                    feature["bar_volume_ratio"] = value
                self.assertIsNone(self.attach(rows, membership))
        rows, membership = pressure_rows()
        rows[0]["_imbalance_features"]["candle"]["estimated_imbalance_pct"] = 20
        rows[0]["_imbalance_features"]["bar_volume_ratio"] = 1.5
        rows[0]["depth_quantity_imbalance"] = .2
        self.assertIsNotNone(self.attach(rows, membership))

    def test_opposite_resting_quantity_and_excluded_rows_clear_old_pressure_only(self):
        overrides = [{key: False} for key in ("fresh", "liquidity_eligible", "depth_valid")]
        overrides += [{"isIndex": True}, {"feature_mode": "regular"},
                      {"_imbalance_features": None}, {"depth_quantity_imbalance": -.5}]
        for changes in overrides:
            with self.subTest(changes=changes):
                rows, membership = pressure_rows()
                row = rows[0]
                row.update(changes, imbalance={"status": "confirmed"},
                           booster={"status": "eligible", "keep": True},
                           building={"status": "building", "keep": True})
                before = copy.deepcopy(row)
                self.assertIsNone(self.attach(rows, membership))
                for key in ("booster", "building", "ltp", "change", "depth_quantity_imbalance"):
                    self.assertEqual(row[key], before[key])


class PressureCoverageTests(PressureCase):
    def test_current_coverage_requires_every_exact_slot_from_0915(self):
        for index in (0, 1, 2, 3):
            for kind in ("missing", "off_grid"):
                with self.subTest(index=index, kind=kind):
                    dataset = pressure_history()
                    frame = dataset["intraday"]
                    position = frame.index[-4 + index]
                    if kind == "missing":
                        dataset["intraday"] = frame.drop(index=position)
                    else:
                        frame.loc[position, "date"] += timedelta(seconds=1)
                    self.assertIsNone(self.features(dataset))

    def test_exact_close_cutoff_excludes_forming_and_future_contamination(self):
        boundary = self.now.replace(second=0)
        dataset = pressure_history()
        before = self.features(dataset, boundary)
        last = dataset["intraday"].iloc[-1]["date"]
        self.assertEqual(last + timedelta(minutes=5), boundary)
        self.assertFalse(self.features(dataset, boundary - timedelta(microseconds=1))["breakout"])
        extras = [dict(candle(boundary + timedelta(minutes=offset), close=9000, volume=1e9),
                       source="sampled_ticks") for offset in (0, 5)]
        dataset["intraday"] = engine.normalize(dataset["intraday"].to_dict("records") + extras)
        self.assertEqual(self.features(dataset, boundary), before)
        raw = engine.imbalance_features(dataset["intraday"], boundary, self.settings)
        self.assertIsNone(raw, "Direct feature input must be complete, not include future slots")

    def test_pressure_requires_three_closed_bars_and_an_open_weekday_market(self):
        dataset = pressure_history()
        self.assertIsNone(self.features(dataset, self.now.replace(minute=29, second=59)))
        self.assertIsNotNone(self.features(dataset, self.now.replace(minute=30, second=0)))
        for now in (self.now.replace(hour=8), self.now.replace(hour=15, minute=30),
                    self.now + timedelta(days=5)):
            with self.subTest(now=now):
                self.assertIsNone(self.features(dataset, now))
        self.assertIsNone(engine.imbalance_features(engine.normalize([]), self.now, self.settings))

    def test_valid_pressure_bar_requires_finite_nonboolean_consistent_ohlcv(self):
        base = dict(candle(self.now, close=100, opening=99.9), source="broker_history")
        self.assertTrue(engine.valid_pressure_bar(base))
        self.assertTrue(engine.valid_pressure_bar(dict(base, volume=0)))
        self.assertTrue(engine.valid_pressure_bar(dict(base, open=100, high=100, low=100)))
        changes = [{key: value} for key in engine.COLUMNS[1:]
                   for value in (None, float("nan"), float("inf"), -float("inf"), True, "bad")]
        changes += [{"open": 102}, {"close": 98}, {"high": 98}, {"low": 102},
                    {"low": 0}, {"volume": -1}]
        for overrides in changes:
            with self.subTest(overrides=overrides):
                self.assertFalse(engine.valid_pressure_bar(dict(base, **overrides)))

    def test_invalid_current_ohlcv_rejects_entire_pressure_feature(self):
        for changes in ({"open": 1000}, {"close": 1}, {"high": float("inf")},
                        {"volume": float("inf")}, {"volume": -1}, {"low": 0}):
            with self.subTest(changes=changes):
                dataset = pressure_history()
                for key, value in changes.items():
                    dataset["intraday"].loc[dataset["intraday"].index[-2], key] = value
                self.assertIsNone(self.features(dataset))

    def test_zero_and_flat_latest_bars_are_valid_but_cannot_be_candidates(self):
        for kind in ("zero", "flat", "all_zero"):
            with self.subTest(kind=kind):
                dataset = pressure_history()
                frame = dataset["intraday"]
                if kind == "all_zero":
                    frame.loc[frame.index[-4:], "volume"] = 0
                elif kind == "zero":
                    frame.loc[frame.index[-1], "volume"] = 0
                else:
                    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = 100
                feature = self.features(dataset)
                if kind == "all_zero":
                    self.assertIsNone(feature)
                else:
                    self.assertEqual(feature["side"], 0)
                    self.assertEqual(feature["bar_volume_ratio"], 0 if kind == "zero" else 2)
                    self.assertEqual(feature["candle"]["estimated_imbalance_pct"], None if kind == "zero" else 0)
                rows, _ = self.candidate(dataset=dataset)
                self.assertIsNone(rows[0]["imbalance"])


class PressureAlignmentTests(PressureCase):
    def test_independent_sector_excludes_subject_and_requires_eighty_percent(self):
        for side in (1, -1):
            for count in (0, 3, 4, 5):
                with self.subTest(side=side, count=count):
                    rows, membership = pressure_rows(side)
                    rows[0]["change"] = -side * 100
                    rows = rows[:1 + count]
                    signal = self.attach(rows, membership)
                    sector = signal["sector"]
                    self.assertEqual(sector["definition"], "excludes_subject")
                    self.assertEqual((sector["peers"], sector["total_peers"]), (count, 5))
                    self.assertEqual(sector["coverage"], count / 5)
                    self.assertEqual(sector["mean"], side * .5 if count >= 4 else None)
                    check = signal["checks"][-1]
                    self.assertIs(check["passed"], True if count >= 4 else None)
                    self.assertEqual(signal["score"], 100 if count >= 4 else 90)
                    self.assertEqual(signal["passed"], 8 if count >= 4 else 7)
                    self.assertEqual(signal["status"], "confirmed" if count >= 4 else "watch")
                    self.assertEqual(signal["alert"]["eligible"], count >= 4)

    def test_sector_mean_is_directional_and_subject_cannot_self_confirm(self):
        for side in (1, -1):
            for mean in (-side * .5, 0, side * .5):
                with self.subTest(side=side, mean=mean):
                    rows, membership = pressure_rows(side)
                    rows[0]["change"] = side * 1000
                    for peer in rows[1:]:
                        peer["change"] = mean
                    signal = self.attach(rows, membership)
                    self.assertEqual(signal["sector"]["mean"], mean)
                    self.assertEqual(signal["checks"][-1]["passed"], mean * side > 0)
        rows, membership = pressure_rows()
        signal = self.attach(rows[:1], membership)
        self.assertEqual(signal["sector"]["coverage"], 0)
        self.assertIsNone(signal["sector"]["mean"])
        self.assertFalse(signal["alert"]["eligible"])

    def test_sector_denominator_uses_membership_and_ignores_other_sector_outsiders(self):
        rows, membership = pressure_rows()
        outsiders = [context_row("OUTSIDE", now=self.now, change=-100,
                                 quote_received_at=self.now.isoformat()),
                     context_row("OTHER", now=self.now, sector="IT", change=-100,
                                 quote_received_at=self.now.isoformat())]
        membership["OTHER"] = "IT"
        signal = self.attach(rows + outsiders, membership)
        self.assertEqual(signal["sector"]["mean"], .5)
        self.assertEqual((signal["sector"]["peers"], signal["sector"]["total_peers"]), (5, 5))
        self.assertEqual(signal["score"], 100)

    def test_sector_rejects_stale_source_receipt_missing_and_nonfinite_peer_changes(self):
        changes = [{key: at} for key in ("quote_as_of", "quote_received_at")
                   for at in (None, "invalid", (self.now - timedelta(seconds=21)).isoformat(),
                              (self.now + timedelta(seconds=1)).isoformat(),
                              (self.now - timedelta(days=1)).isoformat())]
        changes += [{"fresh": False}, {"change": None}, {"change": float("inf")}]
        for override in changes:
            with self.subTest(override=override):
                rows, membership = pressure_rows()
                rows[1].update(override)
                rows[2].update(override)
                signal = self.attach(rows, membership)
                self.assertEqual(signal["sector"]["coverage"], .6)
                self.assertIsNone(signal["sector"]["mean"])
                self.assertEqual(signal["score"], 90)
                self.assertFalse(signal["alert"]["eligible"])

    def test_doji_and_opposite_body_break_latest_consecutive_sequence(self):
        for side in (1, -1):
            for kind, expected in (("previous_doji", 1), ("previous_opposite", 1), ("last_doji", 0)):
                with self.subTest(side=side, kind=kind):
                    dataset = pressure_history(side)
                    frame = dataset["intraday"]
                    position = frame.index[-1 if kind == "last_doji" else -2]
                    close = frame.loc[position, "close"]
                    frame.loc[position, "open"] = close + side * .01 if kind == "previous_opposite" else close
                    rows, _ = self.candidate(side, dataset=dataset)
                    signal = rows[0]["imbalance"]
                    self.assertEqual(signal["consecutive"], expected)
                    self.assertFalse(signal["checks"][6]["passed"])
                    self.assertEqual(signal["status"], "watch")
                    self.assertEqual(signal["score"], 90)

    def test_opening_range_requires_new_strict_close_cross_not_wick_or_equality(self):
        for side in (1, -1):
            for kind, crossed in (("cross", True), ("previous_equal", True),
                                   ("wick", False), ("equal", False)):
                with self.subTest(side=side, kind=kind):
                    dataset = pressure_history(side)
                    frame = dataset["intraday"]
                    latest, previous = frame.index[-1], frame.index[-2]
                    if kind in ("wick", "equal"):
                        close = 100 - side * .01 if kind == "wick" else 100
                        frame.loc[latest, ["open", "high", "low", "close"]] = (
                            [99.85, 100.1, 99.7, close] if side > 0 else [100.15, 100.3, 99.9, close])
                    if kind == "previous_equal":
                        close = 100
                        frame.loc[previous, "close"] = close
                        frame.loc[previous, "high" if side > 0 else "low"] = close
                    feature = self.features(dataset)
                    self.assertEqual(feature["breakout"], crossed)
                    rows, _ = self.candidate(side, dataset=dataset)
                    signal = rows[0]["imbalance"]
                    self.assertEqual(signal["checks"][5]["passed"], crossed)
                    self.assertEqual(signal["alert"]["eligible"], crossed)

    def test_already_crossed_previous_close_is_not_a_new_breakout(self):
        now = self.now + timedelta(minutes=5)
        for side in (1, -1):
            with self.subTest(side=side):
                dataset = pressure_history(side)
                latest = dict(dataset["intraday"].iloc[-1])
                latest.update(date=self.now.replace(second=0), open=100 + side * .3,
                              close=100 + side * .35)
                dataset["intraday"] = engine.normalize(dataset["intraday"].to_dict("records") + [latest])
                self.assertFalse(self.features(dataset, now)["breakout"])
                rows, _ = self.candidate(side, dataset=dataset, now=now)
                self.assertEqual(rows[0]["imbalance"]["status"], "watch")
                self.assertFalse(rows[0]["imbalance"]["alert"]["eligible"])

    def test_closed_vwap_momentum_and_slope_are_independent_of_live_broker_average(self):
        for side in (1, -1):
            dataset = pressure_history(side)
            current = dataset["intraday"].iloc[-4:].to_dict("records")
            typical = [(bar["high"] + bar["low"] + bar["close"]) / 3 for bar in current]
            expected = sum(price * bar["volume"] for price, bar in zip(typical, current)) / 500
            before = sum(typical[:3]) / 3
            for average in (100 + side * 5, None):
                with self.subTest(side=side, average=average):
                    tick = pressure_quote(side, average_price=average)
                    # Missing live traded average disables turnover, not the closed estimate.
                    row = engine.build_row("SYNTH", "BANK", dataset, tick, self.now, self.settings)
                    feature = row["_imbalance_features"]
                    self.assertAlmostEqual(feature["closed_vwap_estimate"], expected)
                    self.assertAlmostEqual(feature["vwap_slope_pct"], (expected / before - 1) * 100)
                    self.assertAlmostEqual(feature["momentum_pct"], (current[-1]["close"] / current[-3]["close"] - 1) * 100)
                    self.assertGreater(feature["closed_vwap_gap_pct"] * side, 0)
                    self.assertGreater(feature["vwap_slope_pct"] * side, 0)
                    self.assertEqual(row["live_vwap"], average)
                    self.assertAlmostEqual(row["vwap"], average if average is not None else expected)
                    if average is not None:
                        rows, _ = self.candidate(side, dataset=dataset, tick=tick)
                        self.assertEqual(rows[0]["imbalance"]["status"], "confirmed")
                        self.assertEqual(rows[0]["imbalance"]["vwap_source"], "ohlcv_hlc3")
                        self.assertEqual(rows[0]["imbalance"]["live_vwap"], average)

    def test_optional_alignment_checks_require_strict_positive_directional_values(self):
        for side in (1, -1):
            for field, check in (("closed_vwap_gap_pct", "vwap"),
                                 ("momentum_pct", "momentum"), ("vwap_slope_pct", "momentum")):
                for value in (0, -side * .1, side * .1):
                    with self.subTest(side=side, field=field, value=value):
                        rows, membership = pressure_rows(side)
                        rows[0]["_imbalance_features"][field] = value
                        signal = self.attach(rows, membership)
                        checks = {item["key"]: item["passed"] for item in signal["checks"]}
                        self.assertEqual(checks[check], value * side > 0)
                        self.assertEqual(signal["score"], 100 if value * side > 0 else 90)
                        self.assertEqual(signal["alert"]["eligible"], value * side > 0)

    def test_reversed_live_price_keeps_pressure_watch_but_never_confirms_alert(self):
        for side in (1, -1):
            for price in (100, 100 - side * .01):
                with self.subTest(side=side, price=price):
                    rows, membership = pressure_rows(side)
                    rows[0]["ltp"] = price
                    signal = self.attach(rows, membership)
                    self.assertEqual(signal["score"], 100)
                    self.assertEqual(signal["status"], "watch")
                    self.assertFalse(signal["alert"]["eligible"])


class PressureFreshnessTests(PressureCase):
    def test_expiry_is_earliest_depth_source_receipt_sector_cache_or_bar_deadline(self):
        cases = (("source", 19, 0, 0, 20, 30, 1), ("receipt", 0, 18, 0, 20, 30, 2),
                 ("peer_source", 0, 0, 17, 20, 30, 3),
                 ("peer_receipt", 0, 0, 16, 20, 30, 4),
                 ("cache", 0, 0, 0, 600, 7, 7), ("bar", 0, 0, 0, 600, 600, 295))
        for label, source_age, receipt_age, peer_age, stale, cache, seconds in cases:
            with self.subTest(deadline=label):
                self.settings = dict(SETTINGS, quote_stale_sec=stale, cache_stale_sec=cache)
                rows, membership = pressure_rows()
                rows[0]["depth_as_of"] = (self.now - timedelta(seconds=source_age)).isoformat()
                rows[0]["depth_received_at"] = (self.now - timedelta(seconds=receipt_age)).isoformat()
                rows[1]["quote_received_at" if label == "peer_receipt" else "quote_as_of"] = (
                    self.now - timedelta(seconds=peer_age)).isoformat()
                signal = self.attach(rows, membership)
                self.assertEqual(engine.timestamp(signal["expires_at"]), self.now + timedelta(seconds=seconds))
                self.assertEqual(engine.timestamp(signal["alert"]["expires_at"]),
                                 min(self.now + timedelta(seconds=seconds), self.now.replace(second=0) + timedelta(seconds=90)))

    def test_quote_age_boundary_is_inclusive_but_no_pressure_has_zero_remaining_life(self):
        for key in ("source_at", "received_at"):
            tick = pressure_quote(**{key: (self.now - timedelta(seconds=20)).isoformat()})
            self.assertTrue(engine.quote_fresh(tick, self.now, 20))
            rows, _ = self.candidate(tick=tick)
            self.assertIsNone(rows[0]["imbalance"])

    def test_active_expiry_and_latest_five_minute_cutoff_are_exclusive(self):
        rows, _ = self.candidate()
        row = rows[0]
        expiry = engine.timestamp(row["imbalance"]["expires_at"])
        self.assertTrue(engine.imbalance_active(row, expiry - timedelta(microseconds=1), self.settings))
        self.assertFalse(engine.imbalance_active(row, expiry, self.settings))
        row["imbalance"]["expires_at"] = (self.now + timedelta(minutes=10)).isoformat()
        self.settings.update(quote_stale_sec=600, cache_stale_sec=600)
        self.assertFalse(engine.imbalance_active(row, self.now.replace(minute=40, second=0), self.settings))

    def test_active_revalidates_quote_receipt_session_feature_computation_and_feed(self):
        rows, _ = self.candidate()
        baseline = rows[0]
        changes = [("row", key, value) for key in ("quote_as_of", "quote_received_at")
                   for value in (None, "invalid", (self.now - timedelta(seconds=21)).isoformat(),
                                 (self.now + timedelta(seconds=1)).isoformat(),
                                 (self.now - timedelta(days=1)).isoformat())]
        changes += [("row", "session_date", None), ("row", "session_date", "2026-09-25"),
                    ("row", "fresh", False), ("row", "isIndex", True),
                    ("row", "feature_mode", "regular"), ("signal", "status", "invalidated")]
        changes += [("signal", key, value) for key in ("as_of", "feature_as_of")
                    for value in (None, "invalid", (self.now + timedelta(seconds=1)).isoformat(),
                                  (self.now - timedelta(days=1)).isoformat())]
        changes += [("signal", "expires_at", value) for value in
                    (None, "invalid", self.now.isoformat(), (self.now - timedelta(seconds=1)).isoformat())]
        changes += [("signal", "as_of", (self.now - timedelta(seconds=31)).isoformat())]
        for target, key, value in changes:
            with self.subTest(target=target, key=key, value=value):
                row = copy.deepcopy(baseline)
                (row if target == "row" else row["imbalance"])[key] = value
                self.assertFalse(engine.imbalance_active(row, self.now, self.settings))
        self.assertFalse(engine.imbalance_active(baseline, self.now, self.settings, live=False))
        self.assertFalse(engine.imbalance_active({}, self.now, self.settings))

    def test_active_computation_cache_age_boundary_is_inclusive(self):
        rows, _ = self.candidate()
        row = rows[0]
        row["imbalance"]["as_of"] = (self.now - timedelta(seconds=30)).isoformat()
        self.assertTrue(engine.imbalance_active(row, self.now, self.settings))
        row["imbalance"]["as_of"] = (self.now - timedelta(seconds=30, microseconds=1)).isoformat()
        self.assertFalse(engine.imbalance_active(row, self.now, self.settings))


class PressureCacheTests(PressureCase):
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

    def test_warm_quote_refresh_reuses_pressure_features_without_rebuilding(self):
        self.seed_history()
        with patch.object(server, "build_row", wraps=engine.build_row) as builder, \
                patch.object(engine, "imbalance_features", wraps=engine.imbalance_features) as features:
            server._refresh_scan_cache(self.now)
            self.assertEqual(builder.call_count, 12)
            self.assertEqual(features.call_count, 6)
            before = copy.deepcopy(server.CACHE["intraday"][0])
            self.assertEqual(before["imbalance"]["status"], "confirmed")
            later = self.now + timedelta(seconds=1)
            self.clock.return_value = later
            for token in server.TICKS:
                server.TICKS[token] = pressure_quote(now=later, ltp=100.35, average_price=100.05)
            server.TICKS[1]["depth"]["buy"] = [{"price": 100.33, "quantity": 4000}] * 5
            server.TICKS[1]["depth"]["sell"] = [{"price": 100.37, "quantity": 1000}] * 5
            server._refresh_scan_cache(later)
            self.assertEqual(builder.call_count, 12)
            self.assertEqual(features.call_count, 6)
        row = next(row for row in server.CACHE["intraday"] if row["symbol"] == "SYNTH")
        signal = row["imbalance"]
        self.assertEqual((row["ltp"], row["quote_received_at"], signal["live_vwap"]),
                         (100.35, later.isoformat(), 100.05))
        self.assertEqual(signal["book"]["imbalance_pct"], 60)
        self.assertEqual(signal["book"]["received_at"], later.isoformat())
        for key in ("candle", "feature_as_of", "bar_volume_ratio", "closed_vwap_estimate", "momentum_pct"):
            self.assertEqual(signal[key], before["imbalance"][key], key)
        self.assertEqual(signal["alert"]["id"], before["imbalance"]["alert"]["id"])

    def test_warm_refresh_removes_pressure_when_new_depth_is_unaligned(self):
        self.seed_history()
        server._refresh_scan_cache(self.now)
        self.assertTrue(all(row["imbalance"] for row in server.CACHE["intraday"]))
        for token in server.TICKS:
            server.TICKS[token] = pressure_quote(-1)
        server._refresh_scan_cache(self.now)
        self.assertTrue(all(row["imbalance"] is None for row in server.CACHE["intraday"]))

    def test_history_source_replacement_rebuilds_provisional_features(self):
        dataset = self.seed_history()
        frame = dataset["intraday"].copy()
        frame.loc[frame.index[-1], "source"] = "sampled_ticks"
        server.HISTORY[1] = dict(dataset, intraday=frame)
        with patch.object(server, "build_row", wraps=engine.build_row) as builder:
            server._refresh_scan_cache(self.now)
            subject = next(row for row in server.CACHE["intraday"] if row["symbol"] == "SYNTH")
            self.assertEqual(subject["imbalance"]["status"], "provisional")
            server.HISTORY[1] = dataset
            server._refresh_scan_cache(self.now)
            self.assertEqual(builder.call_count, 14)
        subject = next(row for row in server.CACHE["intraday"] if row["symbol"] == "SYNTH")
        self.assertEqual(subject["imbalance"]["status"], "confirmed")

    def test_publication_time_invalidates_pressure_before_logging_or_api_delivery(self):
        self.seed_history()
        self.clock.return_value = self.now + timedelta(seconds=21)
        server._refresh_scan_cache(self.now)
        self.record_alerts.assert_not_called()
        self.record_signals.assert_not_called()
        for row in server.CACHE["intraday"]:
            self.assertEqual(row["imbalance"]["status"], "invalidated")
            self.assertFalse(row["imbalance"]["alert"]["eligible"])
        self.assertEqual(server.CACHE["updated_at"], self.clock.return_value.isoformat())

    def test_publication_on_new_bar_cutoff_invalidates_even_with_fresh_quote_budget(self):
        self.seed_history()
        server.SETTINGS.update(quote_stale_sec=600, cache_stale_sec=600)
        published = self.now.replace(minute=40, second=0)
        self.clock.return_value = published
        server._refresh_scan_cache(self.now)
        self.record_alerts.assert_called_once()
        rows, at = self.record_alerts.call_args.args
        self.assertEqual(at, published)
        self.assertTrue(all(row["imbalance"]["status"] == "invalidated" for row in rows))
        self.assertTrue(all(not row["imbalance"]["alert"]["eligible"] for row in rows))

    def test_cache_v3_preserves_source_provenance_roundtrip_only_in_memory(self):
        dataset = pressure_history()
        dataset["intraday"].loc[dataset["intraday"].index[-1], "source"] = "sampled_ticks"
        server.HISTORY[1] = dataset
        server.HISTORY_SEED_DATE = self.now.date().isoformat()
        path = Path("memory-only-history-cache.json.gz")
        writer = io.StringIO()
        with patch.object(server, "HISTORY_CACHE_PATH", path), \
                patch.object(Path, "mkdir"), patch.object(Path, "replace") as replace, \
                patch.object(server.gzip, "open") as gzip_open:
            gzip_open.return_value.__enter__.return_value = writer
            SAVE_HISTORY_CACHE()
            payload = json.loads(writer.getvalue())
            self.assertEqual(payload["version"], 3)
            self.assertEqual(payload["history"]["SYNTH"]["intraday"][-1]["source"], "sampled_ticks")
            replace.assert_called_once_with(path)
            gzip_open.return_value.__enter__.return_value = io.StringIO(writer.getvalue())
            server.HISTORY.clear()
            with patch.object(Path, "exists", return_value=True), \
                    patch.object(Path, "stat", return_value=SimpleNamespace(st_size=len(writer.getvalue()))):
                self.assertTrue(LOAD_HISTORY_CACHE())
        self.assertTrue(server.HISTORY[1]["intraday"].equals(dataset["intraday"]))

    def test_old_cache_versions_are_rejected_in_memory_not_relabelled_broker_history(self):
        for version in (1, 2):
            with self.subTest(version=version):
                payload = {"version": version, "seed_date": self.now.date().isoformat(),
                           "history": {"SYNTH": {"intraday": []}}}
                with patch.object(Path, "exists", return_value=True), \
                        patch.object(Path, "stat", return_value=SimpleNamespace(st_size=100)), \
                        patch.object(server.gzip, "open") as gzip_open:
                    gzip_open.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))
                    self.assertFalse(LOAD_HISTORY_CACHE())
                self.assertEqual(server.HISTORY, {})


class PressureApiTests(PressureCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.login().status_code, 200)
        rows, _ = self.candidate()
        public = engine.public_row(rows[0])
        self.seed_cache([public])
        server.CACHE["updated_at"] = self.now.isoformat()
        server.CACHE["context"].update(as_of=self.now.isoformat(), session_date=self.now.date().isoformat())
        server.TICKS[1] = pressure_quote()

    def test_api_returns_real_nested_pressure_and_settings_with_version(self):
        before = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["rows"][0]["imbalance"], before["intraday"][0]["imbalance"])
        self.assertEqual(payload["imbalance_settings"], {
            "experimental": True, "score_version": "pressure-v1", "min_pct": 20,
            "min_bar_rvol": 1.5, "baseline_sessions": 10, "sector_coverage": .8})
        self.assertEqual(self.strict_json(self.client.get("/api/health"))["version"], "premium-2.4-session-pressure")
        self.assertEqual(payload["booster_settings"]["min_rvol"], SETTINGS["booster_rvol"])
        self.assertEqual(server.CACHE, before)
        self.record_alerts.assert_not_called()

    def test_api_deepcopies_pressure_checks_alert_book_candle_and_sector(self):
        before = copy.deepcopy(server.CACHE)
        with patch.object(server, "jsonify", wraps=server.jsonify) as render:
            self.assertEqual(self.scan().status_code, 200)
        served = render.call_args.kwargs["rows"][0]["imbalance"]
        cached = server.CACHE["intraday"][0]["imbalance"]
        self.assertIsNot(served, cached)
        for key in ("checks", "alert", "book", "candle", "sector"):
            self.assertIsNot(served[key], cached[key], key)
        self.assertIsNot(served["checks"][0], cached["checks"][0])
        served["checks"][0]["passed"] = False
        served["alert"]["eligible"] = False
        served["book"]["bid_quantity"] = -1
        served["candle"]["source"] = "response-only"
        served["sector"]["mean"] = -100
        self.assertEqual(server.CACHE, before)

    def test_regular_and_custom_basket_responses_never_expose_pressure(self):
        before = copy.deepcopy(server.CACHE)
        regular = self.strict_json(self.scan(type="regular"))
        self.assertEqual(len(regular["rows"]), 1)
        self.assertIsNone(regular["rows"][0]["imbalance"])
        with patch.object(server, "INDEX_GROUPS", {"SYNTH BASKET": ["SYNTH"]}):
            baskets = self.strict_json(self.scan(universe="index"))
        self.assertEqual(len(baskets["rows"]), 1)
        self.assertTrue(baskets["rows"][0]["isIndex"])
        self.assertIsNone(baskets["rows"][0]["imbalance"])
        self.assertEqual(server.CACHE, before)

    def test_api_invalidates_quote_receipt_computation_cache_and_feed_on_a_copy(self):
        baseline = copy.deepcopy(server.CACHE)
        old_quote = (self.now - timedelta(seconds=21)).isoformat()
        cases = [("row", key, value) for key in ("quote_as_of", "quote_received_at")
                 for value in (None, "invalid", old_quote,
                               (self.now + timedelta(seconds=1)).isoformat(),
                               (self.now - timedelta(days=1)).isoformat())]
        cases += [("row", "session_date", "2026-09-25"), ("row", "fresh", False),
                  ("signal", "as_of", (self.now - timedelta(seconds=31)).isoformat()),
                  ("signal", "as_of", (self.now + timedelta(seconds=1)).isoformat()),
                  ("cache", "updated_at", (self.now - timedelta(seconds=31)).isoformat()),
                  ("cache", "updated_at", (self.now + timedelta(seconds=1)).isoformat()),
                  ("cache", "updated_at", None), ("feed", "TICKER_CONNECTED", False),
                  ("feed", "SEED_IN_PROGRESS", True), ("feed", "API_KEY", "")]
        for target, key, value in cases:
            with self.subTest(target=target, key=key, value=value):
                server.CACHE = copy.deepcopy(baseline)
                with patch.object(server, "TICKER_CONNECTED", True), \
                        patch.object(server, "SEED_IN_PROGRESS", False), \
                        patch.object(server, "API_KEY", "synthetic-key"):
                    if target == "feed":
                        setattr(server, key, value)
                    else:
                        destinations = {"row": server.CACHE["intraday"][0], "cache": server.CACHE,
                                        "signal": server.CACHE["intraday"][0]["imbalance"]}
                        destinations[target][key] = value
                    before = copy.deepcopy(server.CACHE)
                    payload = self.strict_json(self.scan())
                    signal = payload["rows"][0]["imbalance"]
                    self.assertEqual(payload["live"], target in ("row", "signal"))
                    self.assertEqual(signal["status"], "invalidated")
                    self.assertFalse(signal["alert"]["eligible"])
                    self.assertEqual(server.CACHE, before)
        self.record_alerts.assert_not_called()

    def test_api_missing_malformed_and_exact_expiry_invalidate_nested_alert(self):
        baseline = copy.deepcopy(server.CACHE)
        for expires in ("absent", None, "invalid", self.now.isoformat(),
                        (self.now - timedelta(seconds=1)).isoformat()):
            with self.subTest(expires=expires):
                server.CACHE = copy.deepcopy(baseline)
                signal = server.CACHE["intraday"][0]["imbalance"]
                if expires == "absent":
                    signal.pop("expires_at")
                else:
                    signal["expires_at"] = expires
                before = copy.deepcopy(server.CACHE)
                result = self.strict_json(self.scan())["rows"][0]["imbalance"]
                self.assertEqual(result["status"], "invalidated")
                self.assertFalse(result["alert"]["eligible"])
                self.assertEqual(server.CACHE, before)

    def test_api_new_cutoff_invalidates_pressure_even_when_feed_and_cache_refresh(self):
        later = self.now.replace(minute=40, second=0)
        self.clock.return_value = later
        server.CACHE["updated_at"] = later.isoformat()
        server.TICKS[1] = pressure_quote(now=later)
        row = server.CACHE["intraday"][0]
        row.update(quote_as_of=later.isoformat(), quote_received_at=later.isoformat())
        row["imbalance"].update(as_of=later.isoformat(), expires_at=(later + timedelta(seconds=20)).isoformat())
        before = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["rows"][0]["imbalance"]["status"], "invalidated")
        self.assertFalse(payload["rows"][0]["imbalance"]["alert"]["eligible"])
        self.assertEqual(server.CACHE, before)

    def test_offhours_and_next_date_never_return_active_cached_pressure(self):
        baseline = copy.deepcopy(server.CACHE)
        for later in (self.now.replace(hour=17), self.now + timedelta(days=1)):
            with self.subTest(later=later):
                self.clock.return_value = later
                server.CACHE = copy.deepcopy(baseline)
                server.CACHE["updated_at"] = later.isoformat()
                server.TICKS[1] = pressure_quote(now=later)
                before = copy.deepcopy(server.CACHE)
                payload = self.strict_json(self.scan())
                self.assertEqual(payload["live"], later.hour < 15)
                signal = payload["rows"][0]["imbalance"]
                self.assertEqual(signal["status"], "invalidated")
                self.assertFalse(signal["alert"]["eligible"])
                self.assertEqual(server.CACHE, before)

    def test_sector_response_filter_never_recomputes_or_biases_pressure_score(self):
        rows, membership = self.candidate()
        rows[0]["change"] = 100
        server.CACHE["intraday"] = [engine.public_row(row) for row in rows]
        expected = copy.deepcopy(server.CACHE["intraday"][0]["imbalance"])
        with patch.object(server, "SECTOR_DEFINITIONS", {"BANK": ["SYNTH"], "IT": ["PEER0"]}), \
                patch.object(server, "attach_imbalance", side_effect=AssertionError("API must not rescore filtered peers")):
            all_rows = self.strict_json(self.scan())["rows"]
            filtered = self.strict_json(self.scan(sector="BANK"))["rows"]
        self.assertEqual(len(all_rows), 6)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["imbalance"], expected)
        self.assertEqual(expected["sector"]["mean"], .5)

    def test_api_reversed_cached_price_cannot_serve_an_eligible_pressure_alert(self):
        row = server.CACHE["intraday"][0]
        row["ltp"] = row["imbalance"]["breakout_level"] - .01
        before = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertFalse(payload["rows"][0]["imbalance"]["alert"]["eligible"],
                         "Response-time eligibility must reject a reversed price")
        self.assertEqual(server.CACHE, before)

    def test_api_expired_alert_ttl_is_ineligible_even_if_pressure_snapshot_lives_longer(self):
        self.settings.update(quote_stale_sec=180, cache_stale_sec=180)
        server.SETTINGS.update(self.settings)
        rows, _ = self.candidate()
        server.CACHE["intraday"] = [engine.public_row(rows[0])]
        signal = server.CACHE["intraday"][0]["imbalance"]
        deadline = engine.timestamp(signal["alert"]["expires_at"])
        self.assertLess(deadline, engine.timestamp(signal["expires_at"]))
        self.clock.return_value = deadline
        before = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertFalse(payload["rows"][0]["imbalance"]["alert"]["eligible"],
                         "Alert deadline is independent of the longer pressure snapshot deadline")
        self.assertEqual(server.CACHE, before)


class PressureAlertTests(PressureCase):
    def setUp(self):
        super().setUp()
        self.writer = io.StringIO()
        # Override only the logger's existing disk guards; all writes remain in memory.
        self.mkdir = self.enterContext(patch.object(Path, "mkdir"))
        self.open = self.enterContext(patch.object(Path, "open", autospec=PATH_OPEN))
        self.open.return_value.__enter__.return_value = self.writer

    def test_confirmed_buy_sell_research_records_are_deduplicated_by_pressure_id(self):
        rows, _ = self.candidate()
        signal = rows[0]["imbalance"]
        RECORD_IMBALANCE_ALERTS(rows, self.now)
        RECORD_IMBALANCE_ALERTS(rows, self.now + timedelta(seconds=1))
        self.open.assert_called_once_with(server.DATA_DIR / f"imbalance-alerts-{self.now.date()}.jsonl",
                                          "a", encoding="utf-8")
        self.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        records = [json.loads(line) for line in self.writer.getvalue().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["version"], "premium-2.4-session-pressure")
        self.assertEqual(records[0]["kind"], "imbalance")
        self.assertEqual(records[0]["published_at"], self.now.isoformat())
        self.assertEqual(records[0]["symbol"], "SYNTH")
        self.assertEqual(records[0]["signal"], engine.public_row(signal))
        self.assertIn("not actual executed imbalance or an entry", records[0]["note"])
        self.assertEqual(server.SIGNAL_IDS, {signal["alert"]["id"]})
        sell_rows, _ = self.candidate(-1)
        RECORD_IMBALANCE_ALERTS(sell_rows, self.now)
        self.assertEqual(self.open.call_count, 2)
        self.assertEqual(len(server.SIGNAL_IDS), 2)
        self.assertIn(":-1:", sell_rows[0]["imbalance"]["alert"]["id"])

    def test_provisional_sampled_mixed_watch_and_booster_only_rows_never_log(self):
        cases = []
        for source in ("sampled_ticks", "mixed"):
            dataset = pressure_history()
            frame = dataset["intraday"]
            frame.loc[frame.index[-4:] if source == "sampled_ticks" else frame.index[-1:], "source"] = "sampled_ticks"
            rows, _ = self.candidate(dataset=dataset)
            cases.append(rows)
        rows, membership = pressure_rows()
        self.attach(rows[:1], membership)
        cases.append(rows[:1])
        cases.append([{"symbol": "BOOSTER", "booster": {"status": "eligible", "id": "unrelated"}}])
        for rows in cases:
            RECORD_IMBALANCE_ALERTS(rows, self.now)
        self.open.assert_not_called()
        self.mkdir.assert_not_called()
        self.assertEqual(server.SIGNAL_IDS, set())

    def test_expired_missing_id_and_ineligible_alerts_do_not_consume_ids(self):
        rows, _ = self.candidate()
        baseline = rows[0]
        changes = [{"eligible": False}, {"id": None}, {"id": ""}, {"expires_at": None},
                   {"expires_at": "invalid"}, {"expires_at": self.now.isoformat()},
                   {"expires_at": (self.now - timedelta(seconds=1)).isoformat()}]
        for changeset in changes:
            with self.subTest(changes=changeset):
                row = copy.deepcopy(baseline)
                row["imbalance"]["alert"].update(changeset)
                RECORD_IMBALANCE_ALERTS([row], self.now)
        self.open.assert_not_called()
        self.mkdir.assert_not_called()
        self.assertEqual(server.SIGNAL_IDS, set())

    def test_stale_quote_receipt_feature_or_computation_suppresses_research_publication(self):
        rows, _ = self.candidate()
        baseline = rows[0]
        cases = [("row", "quote_as_of", (self.now - timedelta(seconds=21)).isoformat()),
                 ("row", "quote_received_at", (self.now - timedelta(seconds=21)).isoformat()),
                 ("signal", "as_of", (self.now - timedelta(seconds=31)).isoformat()),
                 ("signal", "feature_as_of", (self.now - timedelta(minutes=5)).isoformat()),
                 ("signal", "status", "invalidated")]
        for target, key, value in cases:
            row = copy.deepcopy(baseline)
            (row if target == "row" else row["imbalance"])[key] = value
            RECORD_IMBALANCE_ALERTS([row], self.now)
        RECORD_IMBALANCE_ALERTS(rows, self.now + timedelta(seconds=21))
        self.open.assert_not_called()
        self.mkdir.assert_not_called()
        self.assertEqual(server.SIGNAL_IDS, set())

    def test_disconnection_seeding_and_closed_market_suppress_publication(self):
        rows, _ = self.candidate()
        with patch.object(server, "TICKER_CONNECTED", False):
            RECORD_IMBALANCE_ALERTS(rows, self.now)
        with patch.object(server, "SEED_IN_PROGRESS", True):
            RECORD_IMBALANCE_ALERTS(rows, self.now)
        RECORD_IMBALANCE_ALERTS(rows, self.now.replace(hour=17))
        self.open.assert_not_called()
        self.mkdir.assert_not_called()
        self.assertEqual(server.SIGNAL_IDS, set())

    def test_price_reversed_or_equal_at_publication_must_not_log_a_confirmed_alert(self):
        for side in (1, -1):
            for offset in (0, -.01):
                with self.subTest(side=side, offset=offset):
                    self.open.reset_mock()
                    server.SIGNAL_IDS.clear()
                    rows, _ = self.candidate(side)
                    row = rows[0]
                    row["ltp"] = row["imbalance"]["breakout_level"] + side * offset
                    RECORD_IMBALANCE_ALERTS(rows, self.now)
                    self.open.assert_not_called()
                    self.assertNotIn(row["imbalance"]["alert"]["id"], server.SIGNAL_IDS)

    def test_failed_in_memory_append_does_not_deduplicate_a_later_successful_retry(self):
        rows, _ = self.candidate()
        self.open.side_effect = OSError("Synthetic append failure")
        with self.assertLogs(server.log, level="ERROR"):
            RECORD_IMBALANCE_ALERTS(rows, self.now)
        self.assertEqual(server.SIGNAL_IDS, set())
        self.open.side_effect = None
        RECORD_IMBALANCE_ALERTS(rows, self.now)
        self.assertEqual(len(self.writer.getvalue().splitlines()), 1)
        self.assertEqual(server.SIGNAL_IDS, {rows[0]["imbalance"]["alert"]["id"]})

    def test_booster_and_pressure_research_have_separate_ids_and_log_files(self):
        rows, _ = self.candidate()
        row = rows[0]
        alert_id = row["imbalance"]["alert"]["id"]
        booster_id = f"{self.now.date()}:SYNTH:1:{row['imbalance']['feature_as_of']}"
        row["booster"] = {"status": "eligible", "id": booster_id,
                          "expires_at": (self.now + timedelta(seconds=10)).isoformat()}
        RECORD_SIGNALS([row], self.now)
        RECORD_IMBALANCE_ALERTS([row], self.now)
        RECORD_SIGNALS([row], self.now)
        RECORD_IMBALANCE_ALERTS([row], self.now)
        self.assertEqual(server.SIGNAL_IDS, {booster_id, alert_id})
        self.assertEqual(self.open.call_count, 2)
        paths = [call.args[0].name for call in self.open.call_args_list]
        self.assertEqual(paths, [f"signals-{self.now.date()}.jsonl",
                                 f"imbalance-alerts-{self.now.date()}.jsonl"])
        records = [json.loads(line) for line in self.writer.getvalue().splitlines()]
        self.assertEqual(records[0]["signal"]["id"], booster_id)
        self.assertEqual(records[1]["signal"]["alert"]["id"], alert_id)
        self.assertEqual(records[1]["kind"], "imbalance")


if __name__ == "__main__":
    unittest.main()
