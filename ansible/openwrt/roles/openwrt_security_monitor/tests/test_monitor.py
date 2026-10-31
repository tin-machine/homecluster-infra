import importlib.util
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "files/security_monitor.py"
spec = importlib.util.spec_from_file_location("security_monitor", SOURCE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "runtime.json"
        self.stamp = datetime(2026, 1, 1, 0, 0).timestamp()
        self.normal = {
            "status": "active", "backend_ver": "1.8.9-r2",
            "active_feeds": ["allowlist.v4", "dshield.v4"],
            "last_run": "mode: reload, date / time: 01/01/2026 00:00:00, duration: 0m 2s",
        }

    def record(self, data):
        self.path.write_text(json.dumps(data))
        return monitor.heartbeat("router-example", runtime=self.path, now=self.stamp + 60)

    def test_normal_and_freshness_scope(self):
        record = self.record(self.normal)
        self.assertEqual(record["banip_status"], "active")
        self.assertEqual(record["banip_run_age_seconds"], 60)
        self.assertEqual(record["banip_run_mode"], "reload")
        self.assertEqual(record["banip_feed_count"], 2)
        self.assertEqual(record["banip_feed_verification"], "unknown")
        self.assertTrue(record["banip_last_run_utc"].endswith("+00:00"))
        self.assertNotIn("banip_error", record)

    def test_missing_runtime_is_unknown(self):
        record = monitor.heartbeat("router-example", runtime=self.path, now=self.stamp)
        self.assertEqual(record["banip_status"], "unknown")
        self.assertEqual(record["banip_error"], "runtime_missing")
        self.assertIsNone(record["banip_run_age_seconds"])

    def test_invalid_values_stay_unknown(self):
        for data in [[], {}, {**self.normal, "status": {"active": True}},
                     {**self.normal, "active_feeds": ["invalid\nfeed"]},
                     {**self.normal, "last_run": "mode: reload, date / time: 32/13/2026 00:00:00,"}]:
            with self.subTest(data=data):
                record = self.record(data)
                self.assertEqual(record["banip_status"], "unknown")
                self.assertEqual(record["banip_error"], "runtime_invalid")

    def test_invalid_json_and_size_limit(self):
        for text in ["{", " " * 32769]:
            self.path.write_text(text)
            record = monitor.heartbeat("router-example", runtime=self.path, now=self.stamp)
            self.assertEqual(record["banip_status"], "unknown")
            self.assertIsNone(record["banip_run_age_seconds"])

    def test_stopped_and_unknown_run_are_not_fresh(self):
        record = self.record({**self.normal, "status": "stopped", "last_run": "-"})
        self.assertEqual(record["banip_status"], "stopped")
        self.assertEqual(record["banip_error"], "last_run_unknown")
        self.assertIsNone(record["banip_run_age_seconds"])

    def test_clock_skew_is_unknown_age(self):
        record = self.record({**self.normal, "last_run": "mode: reload, date / time: 03/01/2026 00:00:00,"})
        self.assertEqual(record["banip_error"], "runtime_clock_skew")
        self.assertIsNone(record["banip_run_age_seconds"])

    def test_config_bounds(self):
        valid = {"sensor": "router-example", "interval_seconds": 60}
        self.path.write_text(json.dumps(valid))
        self.assertEqual(monitor.config(self.path), valid)
        for fields in [{"sensor": ""}, {"sensor": "router\nvalue"}, {"sensor": "router\n"},
                       {"interval_seconds": True}, {"interval_seconds": 59},
                       {"interval_seconds": 301}]:
            self.path.write_text(json.dumps({**valid, **fields}))
            with self.assertRaises(ValueError):
                monitor.config(self.path)

    def test_record_stays_small_with_maximum_feed_list(self):
        record = self.record({**self.normal, "active_feeds": ["a" * 80] * 128})
        self.assertLess(len(json.dumps(record).encode()), 1024)
        self.assertEqual(record["banip_feed_count"], 128)

    def feed_summary(self, status="verified", age=60):
        return {"status": status, "age_seconds": age, "feeds": [
            {"feed": name, "status": status, "verified_at": self.stamp, "age_seconds": age, "last_verified_count": 99}
            for name in ("dshield.v4", "feodo.v4")]}

    def test_verified_feed_evidence_is_optional_and_bounded(self):
        self.path.write_text(json.dumps({**self.normal, "active_feeds": ["a" * 80] * 128}))
        value = self.feed_summary()
        record = monitor.heartbeat("a" * 64, runtime=self.path, now=self.stamp + 60,
                                   feed_verification=True, proof_reader=lambda: value)
        self.assertEqual(record["banip_feed_verification"], "verified")
        self.assertEqual(len(record["banip_feed_results"]), 2)
        self.assertEqual(record["banip_feed_max_age_seconds"], 60)
        self.assertLess(len(json.dumps(record).encode()), 1024)

    def test_malformed_or_unavailable_proof_does_not_become_fresh(self):
        self.path.write_text(json.dumps(self.normal))
        values = [{}, {"status": "verified"}, {"status": "verified", "feeds": []},
                  self.feed_summary(age=48 * 3600 + 1), {"status": "verified", "age_seconds": 0, "feeds": [
                      {"feed": "feodo.v4", "status": "verified", "verified_at": True, "age_seconds": 0, "last_verified_count": 1}]}]
        for value in values:
            with self.subTest(value=value):
                record = monitor.heartbeat("router-example", runtime=self.path, now=self.stamp + 60,
                                           feed_verification=True, proof_reader=lambda: value)
                self.assertEqual(record["banip_feed_verification"], "unknown")
                self.assertIsNone(record["banip_feed_max_age_seconds"])
        def unavailable():
            raise OSError("fixture helper unavailable")
        record = monitor.heartbeat("router-example", runtime=self.path, now=self.stamp + 60,
                                   feed_verification=True, proof_reader=unavailable)
        self.assertEqual(record["banip_status"], "active")
        self.assertEqual(record["banip_feed_verification"], "unknown")

    def test_stale_or_failed_check_preserves_last_verified_age(self):
        self.path.write_text(json.dumps(self.normal))
        for status, age in [("stale", 48 * 3600 + 1), ("error", 60)]:
            value = self.feed_summary(status, age)
            record = monitor.heartbeat("router-example", runtime=self.path, now=self.stamp + age,
                                       feed_verification=True, proof_reader=lambda: value)
            self.assertEqual(record["banip_feed_verification"], status)
            self.assertEqual(record["banip_feed_max_age_seconds"], age)

    def test_invalid_feed_flag_and_oversized_helper_output(self):
        self.path.write_text(json.dumps({"sensor": "router-example", "interval_seconds": 60, "feed_verification_enabled": "yes"}))
        with self.assertRaises(ValueError):
            monitor.config(self.path)
        with patch.object(monitor, "FEED_PROOF_COMMAND", [sys.executable, "-c", "print('x'*2049)"]):
            with self.assertRaises(ValueError):
                monitor.read_feed_proof()

    def test_read_only_proof_child_has_a_wall_budget_and_no_false_success(self):
        with patch.object(monitor, "FEED_PROOF_COMMAND", [sys.executable, "-c", "import time;time.sleep(1)"]), \
                patch.object(monitor, "FEED_PROOF_TIMEOUT", .03):
            with self.assertRaisesRegex(ValueError, "feed_proof_timeout"):
                monitor.read_feed_proof()
        with patch.object(monitor, "FEED_PROOF_COMMAND", [sys.executable, "-c", "raise SystemExit(3)"]):
            with self.assertRaisesRegex(ValueError, "feed_proof_unavailable"):
                monitor.read_feed_proof()


if __name__ == "__main__":
    unittest.main()
