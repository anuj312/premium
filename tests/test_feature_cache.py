import copy
import gzip
import io
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures import NOW, SETTINGS, ServerCase, candle, engine, history, quote, server

SAVE_HISTORY_CACHE = server._save_history_cache
LOAD_HISTORY_CACHE = server._load_history_cache


class FeatureCacheTests(ServerCase):
    def setUp(self):
        super().setUp()
        dataset = history(current_slots=10)
        bars = dataset["intraday"].to_dict("records")
        bars[-1].update(close=104, high=105, low=99, volume=800)
        dataset["intraday"] = engine.normalize(bars)
        server.HISTORY[1] = dataset
        server.TICKS[1] = quote()
        self.builder = self.enterContext(patch.object(server, "build_row", wraps=engine.build_row))
        server._refresh_scan_cache(NOW)
        self.assertEqual(self.builder.call_count, 2)

    def test_warm_refresh_updates_quote_depth_and_turnover_not_closed_indicators(self):
        before = copy.deepcopy(server.CACHE["intraday"][0])
        later = NOW + timedelta(seconds=1)
        self.clock.return_value = later
        depth = {"buy": [{"price": 102.98, "quantity": 4_000}] * 5,
                 "sell": [{"price": 103.02, "quantity": 4_000}] * 5}
        server.TICKS[1] = quote(later, ltp=103, volume=1_500_000,
                                average_price=102, depth=depth)
        server._refresh_scan_cache(later)
        self.assertEqual(self.builder.call_count, 2, "Warm ticks must reuse closed features")
        row = server.CACHE["intraday"][0]
        self.assertEqual(row["ltp"], 103)
        self.assertAlmostEqual(row["change"], 3)
        self.assertEqual(row["quote_as_of"], later.isoformat())
        self.assertEqual(row["bid"], 102.98)
        self.assertEqual(row["ask"], 103.02)
        self.assertEqual(row["turnover"], 153_000_000)
        self.assertNotEqual(row["depth_value"], before["depth_value"])
        self.assertNotEqual(row["ema"], before["ema"])
        self.assertTrue(row["liquidity_eligible"])
        self.assertEqual(server.CACHE["regular"][0]["ratio"], 200)
        for key in ("rsi", "adx", "emaTrend5", "emaTrend15", "feature_as_of",
                    "volume_cutoff", "ratio"):
            with self.subTest(key=key):
                self.assertEqual(row[key], before[key])
        depth["buy"] = [{"price": 103.04, "quantity": 4_000}] * 5
        server.TICKS[1] = quote(later, ltp=103, depth=depth)
        server._refresh_scan_cache(later)
        self.assertEqual(self.builder.call_count, 2)
        self.assertFalse(server.CACHE["intraday"][0]["liquidity_eligible"])
        self.assertIsNone(server.CACHE["intraday"][0]["spread_bps"])

    def test_history_frame_replacement_invalidates_cached_features(self):
        for mode, index, changed_field in (("intraday", -2, "ratio"),
                                            ("regular", -1, "change")):
            with self.subTest(mode=mode):
                previous = server.CACHE[mode][0][changed_field]
                previous_calls = self.builder.call_count
                bars = server.HISTORY[1][mode].to_dict("records")
                bars[index]["close"] += 3
                bars[index]["high"] = bars[index]["close"] + 1
                bars[index]["volume"] += 300
                server.HISTORY[1][mode] = engine.normalize(bars)
                server._refresh_scan_cache(NOW)
                self.assertGreater(self.builder.call_count, previous_calls)
                expected = engine.public_row(engine.build_row(
                    "SYNTH", "OTHER", server.HISTORY[1], server.TICKS[1], NOW, SETTINGS, mode))
                actual = server.CACHE[mode][0]
                self.assertNotEqual(actual[changed_field], previous)
                for key in ("rsi", "adx", "ratio", "emaTrend5", "atrPercent", "change"):
                    self.assertEqual(actual[key], expected[key], key)

    def test_new_five_minute_cut_rebuilds_features_without_history_replacement(self):
        frame = server.HISTORY[1]["intraday"]
        before = copy.deepcopy(server.CACHE["intraday"][0])
        self.assertEqual(before["feature_as_of"], NOW.replace(minute=0).isoformat())
        self.assertEqual(before["ratio"], 2)
        cut = NOW.replace(minute=5, second=0)
        self.clock.return_value = cut
        server.TICKS[1] = quote(cut)
        server._refresh_scan_cache(cut)
        self.assertIs(server.HISTORY[1]["intraday"], frame)
        self.assertGreater(self.builder.call_count, 2)
        row = server.CACHE["intraday"][0]
        self.assertEqual(row["feature_as_of"], cut.isoformat())
        self.assertEqual(row["volume_cutoff"], cut.isoformat())
        self.assertEqual(row["ratio"], 2.6)
        self.assertNotEqual(row["rsi"], before["rsi"])

    def test_new_source_day_invalidates_regular_baseline_with_same_clock_cut(self):
        prior_session = NOW - timedelta(days=3)
        server.HISTORY[1]["regular"] = engine.normalize([
            candle(prior_session - timedelta(days=1), close=80),
            candle(prior_session, close=90)])
        server.TICKS[1] = quote(prior_session, ltp=99, ohlc={"open": 98, "close": 80})
        server._refresh_scan_cache(NOW)
        old = server.CACHE["regular"][0]
        self.assertAlmostEqual(old["change"], 23.75)
        self.assertEqual(old["baseline_date"],
                         (prior_session - timedelta(days=1)).date().isoformat())
        calls = self.builder.call_count
        server.TICKS[1] = quote(NOW, ltp=99, ohlc={"open": 98, "close": 90})
        server._refresh_scan_cache(NOW)
        self.assertGreater(self.builder.call_count, calls)
        row = server.CACHE["regular"][0]
        self.assertAlmostEqual(row["change"], 10)
        self.assertEqual(row["baseline_date"], prior_session.date().isoformat())
        self.assertEqual(row["session_date"], NOW.date().isoformat())

    def test_refresh_uses_frozen_all_symbol_ticks_then_next_refresh_observes_update(self):
        server.SYMBOL_TO_TOKEN = {"FIRST": 1, "SECOND": 2}
        server.TOKEN_TO_SYMBOL = {1: "FIRST", 2: "SECOND"}
        server.HISTORY[2] = history()
        server.TICKS[2] = quote(ltp=102)
        server.FEATURE_CACHE.clear()
        later = NOW + timedelta(seconds=1)

        def build_with_incoming_tick(symbol, sector, dataset, tick, now, settings, mode="intraday"):
            if symbol == "FIRST" and mode == "intraday":
                server.TICKS[2] = quote(later, ltp=150)
            return engine.build_row(symbol, sector, dataset, tick, now, settings, mode)

        self.builder.side_effect = build_with_incoming_tick
        server._refresh_scan_cache(NOW)
        prices = {row["symbol"]: row["ltp"] for row in server.CACHE["intraday"]}
        self.assertEqual(prices["SECOND"], 102)
        self.assertEqual(server.TICKS[2]["ltp"], 150)
        self.clock.return_value = later
        server._refresh_scan_cache(later)
        prices = {row["symbol"]: row["ltp"] for row in server.CACHE["intraday"]}
        self.assertEqual(prices["SECOND"], 150)


class HistoryCompressionTests(ServerCase):
    def test_gzip_json_cache_roundtrip_in_memory_without_runtime_writes(self):
        original = history(prior_sessions=1)
        server.HISTORY[1] = original
        server.HISTORY_SEED_DATE = NOW.date().isoformat()
        directory = Path("/tmp/bcode/memory-only-history")
        cache_path = directory / "history-cache.json.gz"
        writer = io.StringIO()
        with patch.object(server, "DATA_DIR", directory), \
                patch.object(server, "HISTORY_CACHE_PATH", cache_path), \
                patch.object(Path, "mkdir"), patch.object(Path, "replace") as replace, \
                patch.object(server.gzip, "open") as gzip_open:
            gzip_open.return_value.__enter__.return_value = writer
            SAVE_HISTORY_CACHE()
            gzip_open.assert_called_once_with(cache_path.with_suffix(".tmp"), "wt", encoding="utf-8")
            replace.assert_called_once_with(cache_path)
            compressed = gzip.compress(writer.getvalue().encode("utf-8"))
            self.assertEqual(compressed[:2], b"\x1f\x8b")
            gzip_open.return_value.__enter__.return_value = io.StringIO(
                gzip.decompress(compressed).decode("utf-8"))
            server.HISTORY.clear()
            server.HISTORY_SEED_DATE = None
            with patch.object(Path, "exists", return_value=True), \
                    patch.object(Path, "stat", return_value=SimpleNamespace(st_size=len(compressed))):
                self.assertTrue(LOAD_HISTORY_CACHE())
            gzip_open.assert_called_with(cache_path, "rt", encoding="utf-8")
        self.assertEqual(server.HISTORY_SEED_DATE, NOW.date().isoformat())
        for mode in ("intraday", "regular"):
            self.assertTrue(server.HISTORY[1][mode].equals(original[mode]), mode)
