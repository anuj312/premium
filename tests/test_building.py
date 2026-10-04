"""Synthetic, side-effect-guarded regressions for pre-breakout building stocks."""

import copy
from datetime import timedelta
from unittest.mock import patch

from fixtures import (NOW, SETTINGS, ServerCase, cached_row, candle, context_row,
                      engine, history, quote, server, session)

CHECK_KEYS = ("volume", "vwap", "trend5", "trend15", "recent", "sector", "market")


def building_row(side=1, now=NOW):
    opening = now.replace(hour=9, minute=15, second=0, microsecond=0)
    slots = max(0, int((now - opening).total_seconds() // 300))
    bars = session(now, [200] * slots, close=100.0)
    for bar in bars:
        bar.update(high=100.4, low=99.6)
    for bar, high, low in zip(bars, (100.5, 101, 100.7), (99.5, 99.3, 99)):
        bar.update(high=high, low=low)
    return context_row(
        "SYNTH", now=now, direction=side, change=side, ltp=100 + side * .8,
        vwap=100 + side * .5, emaTrend5=side * .1, emaTrend15=side * .1,
        recent=side * .2, indicators_ready=True, liquidity_eligible=True,
        volume_baseline_sessions=20, feature_mode="intraday",
        session_date=now.date().isoformat(),
        feature_as_of=(opening + timedelta(minutes=slots * 5)).isoformat(),
        _session=engine.normalize(bars))


class BuildingTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.settings = dict(SETTINGS)

    def attach(self, row, now=NOW, peers=(), **overrides):
        context = {"regime": "bullish" if row.get("direction") == 1 else "bearish",
                   "as_of": now.isoformat(), "session_date": now.date().isoformat()}
        context.update(overrides)
        engine.attach_building([row, *peers], context, now, self.settings)
        return row["building"]

    def test_long_and_short_payloads_are_symmetric_and_not_trade_signals(self):
        for side in (1, -1):
            with self.subTest(side=side):
                row = building_row(side)
                building = self.attach(row)
                self.assertEqual(building["status"], "building")
                self.assertEqual(building["side"], "long" if side == 1 else "short")
                self.assertEqual(building["trigger"], 101 if side == 1 else 99)
                self.assertEqual((building["range_low"], building["range_high"]), (99, 101))
                self.assertAlmostEqual(building["gap_pct"],
                                       abs(row["ltp"] - building["trigger"]) / building["trigger"] * 100)
                self.assertEqual(tuple(check["key"] for check in building["checks"]), CHECK_KEYS)
                self.assertEqual(building["total"], 7)
                self.assertEqual(building["passed"], 7)
                self.assertEqual(building["passed"], sum(check["passed"] for check in building["checks"]))
                for check in building["checks"]:
                    self.assertIsInstance(check["passed"], bool)
                    self.assertIsInstance(check["label"], str)
                    self.assertTrue(check["label"])
                    self.assertIsInstance(check["detail"], str)
                    self.assertTrue(check["detail"])
                self.assertFalse({"stop", "target", "entry", "probability"} & building.keys())

    def test_attach_mutates_only_building_and_clears_previously_selected_exclusions(self):
        rows = [building_row(), building_row(-1)]
        rows[1].update(symbol="EXCLUDED", fresh=False)
        for row in rows:
            row.update(building={"status": "building", "obsolete": True},
                       booster={"status": "watchlist", "detail": {"keep": True}})
        context = {"regime": "bullish", "as_of": NOW.isoformat(),
                   "session_date": NOW.date().isoformat()}
        before, original_context = copy.deepcopy(rows), copy.deepcopy(context)
        engine.attach_building(rows, context, NOW, self.settings)
        self.assertEqual(rows[0]["building"]["status"], "building")
        self.assertNotIn("obsolete", rows[0]["building"])
        self.assertIsNone(rows[1]["building"])
        self.assertEqual(context, original_context)
        for row, original in zip(rows, before):
            self.assertEqual(set(row), set(original))
            for key, value in original.items():
                if key == "_session":
                    self.assertTrue(row[key].equals(value))
                elif key != "building":
                    self.assertEqual(row[key], value, key)

    def test_first_appearance_is_exactly_0930_after_three_completed_slots(self):
        for side in (1, -1):
            for minute, second, selected in ((29, 59, False), (30, 0, True)):
                with self.subTest(side=side, minute=minute, second=second):
                    now = NOW.replace(hour=9, minute=minute, second=second)
                    result = self.attach(building_row(side, now), now)
                    self.assertEqual(result is not None, selected)

    def test_only_fresh_ready_liquid_current_session_intraday_stocks_are_selected(self):
        exclusions = [{key: False} for key in
                      ("fresh", "indicators_ready", "_current_features", "liquidity_eligible")]
        exclusions += [{"isIndex": True}, {"feature_mode": "regular"},
                       {"session_date": None},
                       {"session_date": (NOW - timedelta(days=1)).date().isoformat()}]
        exclusions += [{"quote_as_of": stamp} for stamp in
                       (None, "invalid", (NOW - timedelta(seconds=21)).isoformat(),
                        (NOW + timedelta(seconds=1)).isoformat(),
                        (NOW - timedelta(days=1)).isoformat())]
        for overrides in exclusions:
            with self.subTest(overrides=overrides):
                row = building_row()
                row.update(overrides, building={"status": "building"})
                self.assertIsNone(self.attach(row))

    def test_direction_must_be_exactly_positive_or_negative_one(self):
        for direction in (None, 0, 2, -2, "long"):
            with self.subTest(direction=direction):
                row = building_row()
                row["direction"] = direction
                self.assertIsNone(self.attach(row))

    def test_positive_finite_prices_and_strictly_aligned_vwap_and_trend5_are_mandatory(self):
        for side in (1, -1):
            overrides = [{key: value} for key in ("ltp", "vwap")
                         for value in (None, 0, -1, float("nan"), float("inf"))]
            overrides += [{"emaTrend5": value} for value in
                          (None, 0, -side * .1, float("nan"), float("inf"))]
            overrides += [{"vwap": 100 + side * .8}, {"vwap": 100 + side * .9}]
            for changes in overrides:
                with self.subTest(side=side, changes=changes):
                    row = building_row(side)
                    row.update(changes)
                    self.assertIsNone(self.attach(row))

    def test_session_requires_every_completed_slot_not_just_a_contiguous_prefix(self):
        for missing in ("absent", "none", "empty", "first", "middle", "last", "previous_day", "off_grid"):
            with self.subTest(missing=missing):
                row = building_row()
                frame = row["_session"].copy()
                if missing == "absent":
                    row.pop("_session")
                elif missing == "none":
                    row["_session"] = None
                elif missing == "empty":
                    row["_session"] = engine.normalize([])
                elif missing in ("first", "middle", "last"):
                    index = {"first": 0, "middle": 4, "last": 8}[missing]
                    row["_session"] = frame.drop(index=index).reset_index(drop=True)
                    row["feature_as_of"] = (row["_session"].iloc[-1]["date"] + timedelta(minutes=5)).isoformat()
                else:
                    frame["date"] += timedelta(days=-1) if missing == "previous_day" else timedelta(seconds=1)
                    row["_session"] = frame
                    row["feature_as_of"] = (frame.iloc[-1]["date"] + timedelta(minutes=5)).isoformat()
                self.assertIsNone(self.attach(row))

    def test_partial_or_future_bars_cannot_supply_building_features(self):
        for minutes in (0, 5):
            with self.subTest(minutes=minutes):
                row = building_row()
                at = NOW.replace(minute=minutes, second=0)
                row["_session"] = engine.normalize(row["_session"].to_dict("records") + [candle(at)])
                row["feature_as_of"] = (at + timedelta(minutes=5)).isoformat()
                self.assertIsNone(self.attach(row))

    def test_feature_as_of_must_equal_latest_completed_candle_end(self):
        latest_end = NOW.replace(minute=0, second=0)
        for stamp in (None, "invalid", latest_end - timedelta(minutes=5),
                      latest_end + timedelta(seconds=1), NOW + timedelta(minutes=1)):
            with self.subTest(stamp=stamp):
                row = building_row()
                row["feature_as_of"] = stamp.isoformat() if hasattr(stamp, "isoformat") else stamp
                self.assertIsNone(self.attach(row))

    def test_context_requires_supported_regime_and_current_fresh_provenance(self):
        overrides = [{"as_of": stamp} for stamp in
                     (None, "invalid", (NOW - timedelta(seconds=21)).isoformat(),
                      (NOW + timedelta(seconds=1)).isoformat(),
                      (NOW - timedelta(days=1)).isoformat())]
        overrides += [{"session_date": None},
                      {"session_date": (NOW - timedelta(days=1)).date().isoformat()}]
        overrides += [{"regime": regime} for regime in (None, "stale", "insufficient_data", "unknown")]
        for changes in overrides:
            with self.subTest(changes=changes):
                self.assertIsNone(self.attach(building_row(), **changes))

    def test_market_must_be_open_and_strictly_before_1445(self):
        times = (NOW.replace(hour=9, minute=14), NOW.replace(hour=14, minute=45),
                 NOW.replace(hour=15, minute=30), NOW + timedelta(days=5))
        for now in times:
            with self.subTest(now=now):
                self.assertIsNone(self.attach(building_row(now=now), now))

    def test_prices_beyond_far_from_or_outside_the_opening_range_are_excluded(self):
        for side in (1, -1):
            trigger = 101 if side == 1 else 99
            prices = (trigger + side * .01, trigger - side * (trigger * .005 + .01),
                      98.99 if side == 1 else 101.01)
            for price in prices:
                with self.subTest(side=side, price=price):
                    row = building_row(side)
                    row.update(ltp=price, vwap=price - side * .2)
                    self.assertIsNone(self.attach(row))

    def test_exact_boundary_and_maximum_half_percent_gap_are_inclusive(self):
        for side in (1, -1):
            for gap in (0, .5):
                with self.subTest(side=side, gap=gap):
                    row = building_row(side)
                    row["_session"].loc[:2, "high"] = 100 if side == 1 else 101
                    row["_session"].loc[:2, "low"] = 99 if side == 1 else 100
                    row.update(ltp=100 - side * gap, vwap=100 - side * gap - side * .2)
                    result = self.attach(row)
                    self.assertEqual(result["trigger"], 100)
                    self.assertAlmostEqual(result["gap_pct"], gap)

    def test_any_completed_same_side_breakout_after_opening_range_excludes_reentry(self):
        for side in (1, -1):
            for index in (3, 8):
                with self.subTest(side=side, index=index):
                    row = building_row(side)
                    close = (101 if side == 1 else 99) + side * .01
                    row["_session"].loc[index, ["close", "high", "low"]] = [close, close + .1, close - .1]
                    self.assertIsNone(self.attach(row))

    def test_wicks_boundary_closes_and_opposite_side_closes_are_not_same_side_breakouts(self):
        for side in (1, -1):
            for kind in ("wick", "touch", "opposite"):
                with self.subTest(side=side, kind=kind):
                    row = building_row(side)
                    trigger = 101 if side == 1 else 99
                    close = 100 if kind == "wick" else trigger
                    if kind == "opposite":
                        close = 98.9 if side == 1 else 101.1
                    row["_session"].loc[3, ["close", "high", "low"]] = [
                        close, max(close, trigger) + 1, min(close, trigger) - 1]
                    self.assertIsNotNone(self.attach(row))

    def test_minimum_rvol_and_valid_history_are_mandatory_but_need_not_pass_volume_check(self):
        for side in (1, -1):
            row = building_row(side)
            row.update(ratio=1.0, volume_baseline_sessions=SETTINGS["min_volume_sessions"])
            result = self.attach(row)
            self.assertFalse(result["checks"][0]["passed"])
            for changes in ([{"ratio": value} for value in
                             (None, .999, 0, float("nan"), float("inf"))] +
                            [{"volume_baseline_sessions": value} for value in
                             (None, 0, SETTINGS["min_volume_sessions"] - 1)]):
                with self.subTest(side=side, changes=changes):
                    row = building_row(side)
                    row.update(changes)
                    self.assertIsNone(self.attach(row))

    def test_hard_thresholds_use_configured_gap_rvol_and_minimum_history(self):
        for side in (1, -1):
            cases = (("building_max_gap_pct", .1, .25),
                     ("building_min_rvol", 2.1, 2.0),
                     ("min_volume_sessions", 21, 20))
            for key, rejecting, accepting in cases:
                with self.subTest(side=side, setting=key):
                    self.settings = dict(SETTINGS, **{key: rejecting})
                    row = building_row(side)
                    self.assertIsNone(self.attach(row))
                    self.settings[key] = accepting
                    self.assertIsNotNone(self.attach(row))

    def test_volume_check_requires_both_booster_rvol_and_full_baseline_thresholds(self):
        for rvol, sessions in ((1.5, 20), (2.5, 22)):
            self.settings.update(booster_rvol=rvol, volume_sessions=sessions)
            for ratio, baseline, passed in ((rvol - .01, sessions, False),
                                           (rvol, sessions - 1, False), (rvol, sessions, True)):
                with self.subTest(rvol=rvol, sessions=sessions, ratio=ratio, baseline=baseline):
                    row = building_row()
                    row.update(ratio=ratio, volume_baseline_sessions=baseline)
                    result = self.attach(row)
                    self.assertEqual(result["checks"][0]["passed"], passed)
                    self.assertEqual(result["passed"], 6 + int(passed))

    def test_partial_readiness_waits_without_inventing_a_trade_or_probability(self):
        for side in (1, -1):
            with self.subTest(side=side):
                row = building_row(side)
                row.update(ratio=1.1, volume_baseline_sessions=10, emaTrend15=None, recent=0)
                peer = context_row("PEER", change=-side * 3, direction=-side)
                result = self.attach(row, peers=[peer], regime="neutral")
                checks = {check["key"]: check["passed"] for check in result["checks"]}
                self.assertEqual(checks, {key: key in ("vwap", "trend5") for key in CHECK_KEYS})
                self.assertEqual((result["passed"], result["total"]), (2, 7))
                self.assertFalse({"stop", "target", "entry", "probability"} & result.keys())

    def test_optional_trend15_and_recent_checks_require_strict_directional_alignment(self):
        for side in (1, -1):
            for field, check_key in (("emaTrend15", "trend15"), ("recent", "recent")):
                for value in (None, 0, -side * .1, side * .1):
                    with self.subTest(side=side, field=field, value=value):
                        row = building_row(side)
                        row[field] = value
                        result = self.attach(row)
                        checks = {check["key"]: check["passed"] for check in result["checks"]}
                        passed = value is not None and value * side > 0
                        self.assertEqual(checks[check_key], passed)
                        self.assertEqual(result["passed"], 6 + int(passed))

    def test_sector_check_uses_directional_mean_and_ignores_stale_and_basket_peers(self):
        for side in (1, -1):
            for change, passed in ((-side * 3, False), (-side, False), (side * .5, True)):
                with self.subTest(side=side, peer_change=change):
                    peers = [context_row("PEER", change=change),
                             context_row("STALE", change=-side * 100, fresh=False),
                             context_row("BASKET", change=-side * 100, isIndex=True),
                             context_row("OTHER", sector="OTHER", change=-side * 100)]
                    result = self.attach(building_row(side), peers=peers)
                    checks = {check["key"]: check["passed"] for check in result["checks"]}
                    self.assertEqual(checks["sector"], passed)
                    self.assertEqual(result["passed"], 6 + int(passed))

    def test_neutral_and_opposite_markets_are_allowed_with_market_check_waiting(self):
        for side in (1, -1):
            for regime in ("bullish", "bearish", "neutral"):
                with self.subTest(side=side, regime=regime):
                    result = self.attach(building_row(side), regime=regime)
                    passed = regime == ("bullish" if side == 1 else "bearish")
                    checks = {check["key"]: check["passed"] for check in result["checks"]}
                    self.assertEqual(checks["market"], passed)
                    self.assertEqual(result["passed"], 6 + int(passed))

    def test_expiry_is_the_earliest_stock_context_cache_candle_or_cutoff_deadline(self):
        cutoff_now = NOW.replace(hour=14, minute=44, second=59)
        cases = (("stock", NOW, 19, 0, 20, 30, NOW + timedelta(seconds=1)),
                 ("context", NOW, 0, 18, 20, 30, NOW + timedelta(seconds=2)),
                 ("cache", NOW, 0, 0, 600, 7, NOW + timedelta(seconds=7)),
                 ("candle", NOW, 0, 0, 600, 600, NOW.replace(minute=5, second=0)),
                 ("cutoff", cutoff_now, 0, 0, 600, 600, cutoff_now.replace(minute=45, second=0)))
        for label, now, stock_age, context_age, quote_limit, cache_limit, expiry in cases:
            with self.subTest(deadline=label):
                self.settings = dict(SETTINGS, quote_stale_sec=quote_limit, cache_stale_sec=cache_limit)
                row = building_row(now=now)
                row["quote_as_of"] = (now - timedelta(seconds=stock_age)).isoformat()
                result = self.attach(row, now, as_of=(now - timedelta(seconds=context_age)).isoformat())
                self.assertEqual(engine.timestamp(result["as_of"]), now)
                self.assertEqual(result["feature_as_of"], row["feature_as_of"])
                self.assertEqual(engine.timestamp(result["expires_at"]), expiry)

    def test_build_refresh_and_baskets_never_reuse_building_payloads(self):
        for mode in ("intraday", "regular"):
            with self.subTest(mode=mode):
                row = engine.build_row("SYNTH", "BANK", history(), quote(), NOW, SETTINGS, mode)
                self.assertIsNone(row["building"])
                row["building"] = {"status": "building", "obsolete": True}
                original = copy.deepcopy(row["building"])
                refreshed = engine.refresh_row(row, quote(), NOW, SETTINGS, mode)
                self.assertIsNone(refreshed["building"])
                self.assertEqual(row["building"], original)
        row = building_row()
        self.attach(row)
        basket = engine.basket_rows([row], {"SYNTH BASKET": ["SYNTH"]})[0]
        self.assertIsNone(basket["building"])


class BuildingApiTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.login().status_code, 200)
        building = {"status": "building", "side": "long", "trigger": 101,
                    "range_low": 99, "range_high": 101, "gap_pct": .198,
                    "passed": 7, "total": 7, "reason": "Synthetic candidate",
                    "as_of": NOW.isoformat(),
                    "feature_as_of": NOW.replace(minute=0, second=0).isoformat(),
                    "expires_at": (NOW + timedelta(seconds=10)).isoformat(),
                    "checks": [{"key": key, "label": key, "passed": True,
                                "detail": "Synthetic readiness"} for key in CHECK_KEYS]}
        self.seed_cache([cached_row(ltp=100.8, vwap=100.5, building=building)])
        server.CACHE["regular"] = [cached_row(building=None)]

    def test_live_api_serves_building_and_exposes_settings_without_changing_booster_thresholds(self):
        original = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["rows"][0]["building"], original["intraday"][0]["building"])
        self.assertEqual(payload["building_settings"]["max_gap_pct"], .5)
        self.assertEqual(payload["building_settings"]["min_rvol"], 1.0)
        self.assertIs(payload["building_settings"]["experimental"], True)
        for key, setting in (("min_rvol", "booster_rvol"), ("baseline_sessions", "volume_sessions"),
                             ("ttl_sec", "signal_ttl_sec")):
            self.assertEqual(payload["booster_settings"][key], SETTINGS[setting])
        self.assertEqual(server.CACHE, original)
        for regime in ("neutral", "bearish"):
            with self.subTest(regime=regime):
                server.CACHE["context"]["regime"] = regime
                before = copy.deepcopy(server.CACHE)
                self.assertEqual(self.strict_json(self.scan())["rows"][0]["building"]["status"], "building")
                self.assertEqual(server.CACHE, before)

    def test_api_invalidates_stale_stock_feed_and_cache_on_a_copy_with_a_reason(self):
        baseline = copy.deepcopy(server.CACHE)
        old_quote = (NOW - timedelta(seconds=SETTINGS["quote_stale_sec"] + 1)).isoformat()
        old_cache = (NOW - timedelta(seconds=SETTINGS["cache_stale_sec"] + 1)).isoformat()
        future = (NOW + timedelta(seconds=1)).isoformat()
        cases = (("row", "quote_as_of", old_quote), ("row", "quote_as_of", None),
                 ("row", "quote_as_of", future), ("row", "fresh", False),
                 ("row", "session_date", (NOW - timedelta(days=1)).date().isoformat()),
                 ("cache", "updated_at", old_cache), ("cache", "updated_at", future),
                 ("cache", "updated_at", None), ("feed", "TICKER_CONNECTED", False),
                 ("tick", "source_at", old_quote))
        for target, key, value in cases:
            with self.subTest(target=target, key=key, value=value):
                server.CACHE = copy.deepcopy(baseline)
                server.TICKS[1] = quote()
                server.TICKER_CONNECTED = True
                if target == "feed":
                    setattr(server, key, value)
                else:
                    destination = {"row": server.CACHE["intraday"][0],
                                   "cache": server.CACHE, "tick": server.TICKS[1]}[target]
                    destination[key] = value
                before = copy.deepcopy(server.CACHE)
                payload = self.strict_json(self.scan())
                result = payload["rows"][0]["building"]
                self.assertEqual(payload["live"], target == "row")
                self.assertEqual(result["status"], "invalidated")
                self.assertTrue(result["reason"])
                self.assertNotEqual(result["reason"], "Synthetic candidate")
                self.assertEqual(server.CACHE, before)

    def test_api_invalidates_stale_or_insufficient_context_despite_fresh_stock_feed_and_cache(self):
        original_context = dict(server.CACHE["context"])
        overrides = [{"as_of": stamp} for stamp in
                     (None, "invalid", (NOW - timedelta(seconds=21)).isoformat(),
                      (NOW + timedelta(seconds=1)).isoformat())]
        overrides += [{"session_date": None},
                      {"session_date": (NOW - timedelta(days=1)).date().isoformat()}]
        overrides += [{"regime": regime} for regime in (None, "stale", "insufficient_data", "unknown")]
        for changes in overrides:
            with self.subTest(changes=changes):
                server.CACHE["context"] = dict(original_context, **changes)
                before = copy.deepcopy(server.CACHE)
                payload = self.strict_json(self.scan())
                self.assertTrue(payload["live"])
                self.assertTrue(payload["rows"][0]["fresh"])
                result = payload["rows"][0]["building"]
                self.assertEqual(result["status"], "invalidated")
                self.assertTrue(result["reason"])
                self.assertEqual(server.CACHE, before)

    def test_api_invalidates_missing_malformed_or_expired_expiry_at_the_exact_boundary(self):
        for expiry in ("absent", None, "invalid", NOW.isoformat(),
                       (NOW - timedelta(seconds=1)).isoformat()):
            with self.subTest(expiry=expiry):
                building = server.CACHE["intraday"][0]["building"]
                if expiry == "absent":
                    building.pop("expires_at", None)
                else:
                    building["expires_at"] = expiry
                before = copy.deepcopy(server.CACHE)
                payload = self.strict_json(self.scan())
                self.assertTrue(payload["live"])
                result = payload["rows"][0]["building"]
                self.assertEqual(result["status"], "invalidated")
                self.assertTrue(result["reason"])
                self.assertEqual(server.CACHE, before)

    def test_response_deepcopies_building_and_nested_checks_not_just_the_outer_row(self):
        before = copy.deepcopy(server.CACHE)
        with patch.object(server, "jsonify", wraps=server.jsonify) as render:
            self.assertEqual(self.scan().status_code, 200)
        served = render.call_args.kwargs["rows"][0]["building"]
        cached = server.CACHE["intraday"][0]["building"]
        self.assertIsNot(served, cached)
        self.assertIsNot(served["checks"], cached["checks"])
        self.assertIsNot(served["checks"][0], cached["checks"][0])
        served["checks"][0]["detail"] = "Response-only change"
        self.assertEqual(server.CACHE, before)

    def test_regular_and_basket_api_rows_never_have_building(self):
        before = copy.deepcopy(server.CACHE)
        regular = self.strict_json(self.scan(type="regular"))
        self.assertEqual(len(regular["rows"]), 1)
        self.assertIsNone(regular["rows"][0]["building"])
        with patch.object(server, "INDEX_GROUPS", {"SYNTH BASKET": ["SYNTH"]}):
            baskets = self.strict_json(self.scan(universe="index"))
        self.assertEqual(len(baskets["rows"]), 1)
        self.assertTrue(baskets["rows"][0]["isIndex"])
        self.assertIsNone(baskets["rows"][0]["building"])
        self.assertEqual(server.CACHE, before)
