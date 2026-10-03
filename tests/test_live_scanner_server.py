import copy
import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

from fixtures import (NOW, PHONE, SESSION, SETTINGS, ServerCase, broker_tick,
                      cached_row, candle, engine, history, quote, server, session)


class TickTests(ServerCase):
    def test_naive_sdk_host_local_timestamps_convert_to_ist(self):
        previous_tz = os.environ.get("TZ")
        try:
            for host_tz in ("UTC0", "EST5"):
                with self.subTest(host_tz=host_tz):
                    os.environ["TZ"] = host_tz
                    time.tzset()
                    naive = datetime.fromtimestamp(NOW.timestamp())
                    self.assertIsNone(naive.tzinfo)
                    self.assertEqual(server._sdk_timestamp(naive), NOW)
                    self.assertTrue(server._update_tick(broker_tick(
                        exchange_timestamp=naive, last_trade_time=naive), NOW))
                    self.assertEqual(server.TICKS[1]["source_at"], NOW.isoformat())
                    self.assertEqual(server.TICKS[1]["trade_at"], NOW.isoformat())
                    self.assertTrue(engine.quote_fresh(server.TICKS[1], NOW, 20))
            self.assertEqual(server._sdk_timestamp(NOW), NOW)
        finally:
            if previous_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous_tz
            time.tzset()

    def test_missing_or_far_future_exchange_timestamp_rejected(self):
        for source in (None, NOW + timedelta(seconds=6)):
            with self.subTest(source=source):
                self.assertFalse(server._update_tick(
                    broker_tick(exchange_timestamp=source), NOW))
                self.assertEqual(server.TICKS, {})
                self.assertEqual(server.LIVE_BARS, {})

    def test_old_exchange_tick_rejected_not_retimestamped_as_new(self):
        old = NOW - timedelta(seconds=SETTINGS["quote_stale_sec"] + 1)
        self.assertFalse(server._update_tick(broker_tick(old), NOW),
                         "Stale exchange ticks must not replace current state or form bars")
        self.assertEqual(server.TICKS, {})
        self.assertEqual(server.LIVE_BARS, {})

    def test_out_of_order_tick_preserves_quote_and_provisional_bars(self):
        self.assertTrue(server._update_tick(broker_tick(), NOW))
        before = copy.deepcopy((server.TICKS, server.LIVE_BARS))
        late = NOW - timedelta(seconds=1)
        self.assertFalse(server._update_tick(broker_tick(late, last_price=90), NOW))
        self.assertEqual((server.TICKS, server.LIVE_BARS), before)

    def test_decreasing_same_day_volume_rejected(self):
        self.assertTrue(server._update_tick(broker_tick(), NOW))
        before = copy.deepcopy((server.TICKS, server.LIVE_BARS))
        later = NOW + timedelta(seconds=1)
        self.assertFalse(server._update_tick(
            broker_tick(later, volume_traded=999_999), later))
        self.assertEqual((server.TICKS, server.LIVE_BARS), before)

    def test_next_session_volume_can_reset_and_old_live_bars_clear(self):
        self.assertTrue(server._update_tick(broker_tick(), NOW))
        tomorrow = NOW + timedelta(days=1)
        self.assertTrue(server._update_tick(broker_tick(tomorrow, volume_traded=100), tomorrow))
        self.assertEqual(server.TICKS[1]["volume"], 100)
        self.assertTrue(all(at.date() == tomorrow.date() for at in server.LIVE_BARS[1]))

    def test_book_only_update_does_not_invent_a_trade_bar(self):
        self.assertTrue(server._update_tick(broker_tick(last_trade_time=None), NOW))
        self.assertEqual(server.LIVE_BARS, {})
        self.assertEqual(server.TICKS[1]["source_at"], NOW.isoformat())

    def test_live_bar_volume_uses_cumulative_delta_not_day_total(self):
        self.assertTrue(server._update_tick(broker_tick(volume_traded=1_000), NOW))
        later = NOW + timedelta(seconds=1)
        self.assertTrue(server._update_tick(broker_tick(later, volume_traded=1_025), later))
        self.assertEqual(next(iter(server.LIVE_BARS[1].values()))["volume"], 25)

    def test_invalid_tick_price_never_enters_json_quote_state(self):
        for price in (None, 0, -1, float("nan"), float("inf")):
            with self.subTest(price=price):
                self.assertFalse(server._update_tick(broker_tick(last_price=price), NOW))
        self.assertEqual(server.TICKS, {})

    def test_interrupted_previous_bar_never_becomes_complete_after_reconnect(self):
        start = NOW.replace(hour=9, minute=20, second=0)
        times = [start - timedelta(seconds=1)]
        times += [start + timedelta(seconds=seconds) for seconds in range(0, 61, 10)]
        times += [start + timedelta(minutes=6), start + timedelta(minutes=6, seconds=10)]
        for index, at in enumerate(times):
            self.assertTrue(server._update_tick(broker_tick(at, volume_traded=1_000 + index), at))
            if start in server.LIVE_BARS[1]:
                self.assertFalse(server.LIVE_BARS[1][start]["complete"],
                                 "The interrupted 09:20 bar must never be promoted")
        self.assertFalse(server.LIVE_BARS[1][start]["continuous"])
        dataset, _tick = server._history_state(1, times[-1])
        self.assertTrue(dataset["intraday"].empty)

    def test_continuous_provisional_bar_completes_only_on_next_bucket(self):
        start = NOW.replace(hour=9, minute=20, second=0)
        times = [start - timedelta(seconds=1)]
        times += [start + timedelta(seconds=seconds) for seconds in range(0, 300, 10)]
        times += [start + timedelta(minutes=4, seconds=59)]
        for index, at in enumerate(times):
            self.assertTrue(server._update_tick(broker_tick(at, volume_traded=1_000 + index), at))
        self.assertFalse(server.LIVE_BARS[1][start]["complete"])
        end = start + timedelta(minutes=5)
        self.assertTrue(server._update_tick(broker_tick(end, volume_traded=2_000), end))
        self.assertTrue(server.LIVE_BARS[1][start]["complete"])
        self.assertFalse(server.LIVE_BARS[1][end]["complete"])
        dataset, _tick = server._history_state(1, end)
        self.assertEqual(list(dataset["intraday"]["date"]), [start])


class HistoryAuthorityTests(ServerCase):
    def test_completed_broker_candle_overrides_same_slot_provisional(self):
        at = NOW.replace(hour=9, minute=55)
        broker = candle(at, close=101, volume=123)
        server.HISTORY[1] = {"intraday": engine.normalize([broker])}
        server.LIVE_BARS[1] = {at: {**candle(at, close=999, volume=9_000_000),
                                   "complete": True}}
        dataset, _tick = server._history_state(1, NOW)
        self.assertEqual(len(dataset["intraday"]), 1)
        self.assertEqual(dataset["intraday"].iloc[0]["close"], 101)
        self.assertEqual(dataset["intraday"].iloc[0]["volume"], 123)

    def test_broker_authority_survives_many_duplicate_timestamp_pairs(self):
        bars = session(NOW - timedelta(days=3), [123] * 75, close=101)
        server.HISTORY[1] = {"intraday": engine.normalize(bars)}
        server.LIVE_BARS[1] = {
            bar["date"]: {**candle(bar["date"], close=999, volume=9_000_000),
                          "complete": True} for bar in bars}
        dataset, _tick = server._history_state(1, NOW)
        self.assertEqual(len(dataset["intraday"]), 75)
        self.assertTrue((dataset["intraday"]["close"] == 101).all(),
                        "Every completed broker candle must win duplicate timestamps")
        self.assertTrue((dataset["intraday"]["volume"] == 123).all())

    def test_incomplete_and_forming_provisional_bars_excluded(self):
        completed = NOW.replace(hour=9, minute=55)
        forming = NOW.replace(minute=0)
        future = NOW.replace(minute=5)
        server.LIVE_BARS[1] = {
            completed: {**candle(completed), "complete": False},
            forming: {**candle(forming), "complete": True},
            future: {**candle(future), "complete": True},
        }
        dataset, _tick = server._history_state(1, NOW)
        self.assertTrue(dataset["intraday"].empty)

    def test_complete_provisional_bar_fills_only_missing_broker_slot(self):
        at = NOW.replace(hour=9, minute=55)
        server.LIVE_BARS[1] = {at: {**candle(at, volume=25), "complete": True}}
        dataset, _tick = server._history_state(1, NOW)
        self.assertEqual(len(dataset["intraday"]), 1)
        self.assertEqual(dataset["intraday"].iloc[0]["volume"], 25)

    def test_history_state_does_not_mutate_broker_frames(self):
        server.HISTORY[1] = history()
        original = server.HISTORY[1]["intraday"].copy()
        dataset, _tick = server._history_state(1, NOW)
        dataset["intraday"].iloc[0, dataset["intraday"].columns.get_loc("close")] = 999
        self.assertTrue(server.HISTORY[1]["intraday"].equals(original))


class AccessTests(ServerCase):
    def test_non_object_json_payloads_are_400_for_login_and_logout(self):
        for route in ("/api/access/login", "/api/access/logout"):
            for payload in ([1], [], "bad", "", 42, 0, True, False, None):
                with self.subTest(route=route, payload=payload):
                    response = self.client.post(route, data=json.dumps(payload),
                                                content_type="application/json")
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json()["error"], "invalid_request")
        self.allowlist.assert_not_called()
        self.assertEqual(server.ACTIVE_ACCESS_SESSIONS, {})

    def test_unauthenticated_scan_is_forbidden(self):
        response = self.client.get("/api/scan")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "access_required")

    def test_unlisted_number_is_forbidden(self):
        response = self.login(phone="0000000001")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "not_allowed")

    def test_invalid_session_identifier_is_rejected(self):
        self.assertEqual(self.login(session_id="short").status_code, 400)

    def test_active_session_conflict_is_409_and_original_still_works(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.login(session_id="test-session-beta-0002").status_code, 409)
        self.assertEqual(self.scan().status_code, 200)

    def test_same_session_login_is_idempotent(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.login().status_code, 200)

    def test_logout_revokes_access_and_releases_single_session_lock(self):
        self.login()
        response = self.client.post("/api/access/logout", json={
            "phone": PHONE, "session_id": SESSION})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.scan().status_code, 403)
        self.assertEqual(self.login(session_id="test-session-beta-0002").status_code, 200)

    def test_wrong_session_cannot_logout_owner(self):
        self.login()
        self.client.post("/api/access/logout", json={
            "phone": PHONE, "session_id": "test-session-beta-0002"})
        self.assertEqual(self.scan().status_code, 200)

    def test_expired_session_is_forbidden(self):
        self.login()
        server.ACTIVE_ACCESS_SESSIONS[PHONE]["ts"] -= server.ACCESS_TTL_SEC + 1
        self.assertEqual(self.scan().status_code, 403)

    def test_phone_normalization_accepts_india_country_prefix(self):
        self.assertEqual(self.login(phone="+91 " + PHONE).status_code, 200)
        self.assertEqual(self.scan().status_code, 200)

    def test_allowlist_has_no_public_endpoint(self):
        for path in ("/numbers.txt", "/static/numbers.txt", "/api/numbers",
                     "/api/access/numbers"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(PHONE, response.get_data(as_text=True))
        self.allowlist.assert_not_called()

    def test_public_health_does_not_disclose_allowlist_or_credentials(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_data(as_text=True)
        for private in (PHONE, "synthetic-key", "synthetic-token", "allowed_access_numbers"):
            self.assertNotIn(private, payload)
        self.allowlist.assert_not_called()

    def test_testing_mode_suppresses_startup_and_security_headers_are_present(self):
        response = self.client.get("/api/health")
        server.initialize_live.assert_not_called()
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")


class ScanTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.login().status_code, 200)
        self.seed_cache()

    def assert_not_eligible(self, payload):
        self.assertFalse(payload["live"])
        self.assertEqual(payload["context"]["regime"], "stale")
        self.assertIsNone(payload["context"]["agreement"])
        for row in payload["rows"]:
            self.assertFalse(row["fresh"])
            self.assertFalse(row["liquidity_eligible"])
            self.assertNotEqual((row.get("booster") or {}).get("status"), "eligible")

    def test_fresh_connected_feed_returns_live_synthetic_cache(self):
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertEqual(payload["status"], "live")
        self.assertTrue(payload["rows"][0]["fresh"])
        self.assertTrue(payload["rows"][0]["liquidity_eligible"])

    def test_expired_cache_invalidates_signals_and_liquidity_without_mutating_cache(self):
        server.CACHE["updated_at"] = (NOW - timedelta(seconds=31)).isoformat()
        original = copy.deepcopy(server.CACHE)
        self.assert_not_eligible(self.strict_json(self.scan()))
        self.assertEqual(server.CACHE, original)

    def test_cache_freshness_boundary_is_inclusive_but_future_cache_is_invalid(self):
        server.CACHE["updated_at"] = (NOW - timedelta(seconds=30)).isoformat()
        self.assertTrue(self.strict_json(self.scan())["live"])
        server.CACHE["updated_at"] = (NOW + timedelta(seconds=1)).isoformat()
        self.assert_not_eligible(self.strict_json(self.scan()))

    def test_offhours_never_serve_eligible_cached_rows(self):
        now = NOW.replace(hour=17)
        self.clock.return_value = now
        server.CACHE["updated_at"] = now.isoformat()
        server.TICKS[1] = quote(now)
        self.assert_not_eligible(self.strict_json(self.scan()))

    def test_disconnected_feed_never_serves_eligible_rows(self):
        server.TICKER_CONNECTED = False
        self.assert_not_eligible(self.strict_json(self.scan()))

    def test_missing_credentials_never_serves_eligible_rows(self):
        with patch.object(server, "API_KEY", ""), patch.object(server, "ACCESS_TOKEN", ""):
            payload = self.strict_json(self.scan())
        self.assertEqual(payload["status"], "missing_credentials")
        self.assert_not_eligible(payload)

    def test_stale_row_invalidated_even_when_other_tick_keeps_feed_live(self):
        server.CACHE["intraday"][0]["quote_as_of"] = (
            NOW - timedelta(seconds=21)).isoformat()
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        row = payload["rows"][0]
        self.assertFalse(row["fresh"])
        self.assertFalse(row["liquidity_eligible"])
        self.assertEqual(row["booster"]["status"], "invalidated")

    def test_signal_expiry_and_missing_expiry_invalidated_at_response_time(self):
        for expiry in (NOW.isoformat(), None):
            with self.subTest(expiry=expiry):
                server.CACHE["intraday"][0]["booster"]["expires_at"] = expiry
                payload = self.strict_json(self.scan())
                self.assertTrue(payload["live"])
                self.assertEqual(payload["rows"][0]["booster"]["status"], "invalidated")

    def test_nifty_filter_returns_all_fifty_members_not_primary_sector_matches(self):
        members = server.SECTOR_DEFINITIONS["NIFTY_50"]
        self.seed_cache([cached_row(symbol, sector=server.PRIMARY_SECTOR.get(symbol, "OTHER"))
                         for symbol in members] + [cached_row("OUTSIDE")])
        payload = self.strict_json(self.scan(sector="NIFTY_50"))
        self.assertEqual(len(payload["rows"]), 50)
        self.assertEqual({row["symbol"] for row in payload["rows"]}, set(members))

    def test_context_independent_of_requested_sector_and_universe(self):
        members = server.SECTOR_DEFINITIONS["NIFTY_50"]
        self.seed_cache([cached_row(symbol) for symbol in members])
        server.CACHE["context"] = {
            "regime": "bullish", "agreement": 80, "coverage": .9,
            "feature_coverage": .8, "votes": {"index_move": 1},
            "as_of": NOW.isoformat(), "session_date": NOW.date().isoformat()}
        original = copy.deepcopy(server.CACHE["context"])
        for query in ({}, {"sector": "BANK"}, {"sector": "IT"},
                      {"sector": "NIFTY_50"}, {"universe": "index"}):
            with self.subTest(query=query):
                self.assertEqual(self.strict_json(self.scan(**query))["context"], original)

    def test_unknown_sector_and_invalid_modes_are_not_silently_accepted(self):
        for query in ({"sector": "DOES_NOT_EXIST"}, {"type": "future"},
                      {"universe": "fake"}):
            with self.subTest(query=query):
                self.assertEqual(self.scan(**query).status_code, 400)

    def test_empty_history_refresh_has_no_fake_json_price(self):
        server.HISTORY.clear()
        server.TICKS.clear()
        server.LIVE_BARS.clear()
        server._refresh_scan_cache(NOW)
        self.record_signals.assert_called_once()
        payload = self.strict_json(self.scan())
        self.assertFalse(payload["live"])
        self.assertEqual(payload["rows"], [])

    def test_index_basket_json_contains_null_not_synthetic_price(self):
        self.seed_cache([cached_row("HDFCBANK")])
        payload = self.strict_json(self.scan(universe="index"))
        self.assertTrue(payload["rows"])
        self.assertTrue(all(row["ltp"] is None for row in payload["rows"]))
        self.assertTrue(all(not row["liquidity_eligible"] for row in payload["rows"]))

    def test_response_never_discloses_phone_or_mock_credentials(self):
        response = self.scan()
        for private in (PHONE, "synthetic-key", "synthetic-token"):
            self.assertNotIn(private, response.get_data(as_text=True))

    def test_expired_context_invalidates_booster_despite_fresh_stock_feed_and_cache(self):
        self.assertEqual(self.strict_json(self.scan())["rows"][0]["booster"]["status"],
                         "eligible")
        later = NOW + timedelta(seconds=21)
        self.clock.return_value = later
        server.TICKS[1] = quote(later)
        server.CACHE["updated_at"] = later.isoformat()
        server.CACHE["intraday"][0]["quote_as_of"] = later.isoformat()
        original = copy.deepcopy(server.CACHE)
        payload = self.strict_json(self.scan())
        self.assertTrue(payload["live"])
        self.assertTrue(payload["rows"][0]["fresh"])
        self.assertTrue(payload["rows"][0]["liquidity_eligible"])
        self.assertEqual(payload["context"]["regime"], "stale")
        self.assertIsNone(payload["context"]["agreement"])
        self.assertEqual(payload["rows"][0]["booster"]["status"], "invalidated")
        self.assertEqual(server.CACHE, original)
