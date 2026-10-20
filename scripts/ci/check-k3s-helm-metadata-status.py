#!/usr/bin/env python3
"""Synthetic label-only Helm metadata probe checks."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "k3s_helm_metadata_status.py"
spec = importlib.util.spec_from_file_location("helm_metadata_status_tested", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

BOOT = "11111111-2222-3333-4444-555555555555"
NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
ROWS = "observability-stg loki superseded 1\nobservability-stg loki deployed 2\narc-systems arc deployed 1\n"


class HelmMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.boot = Path(self.tmp.name) / "boot_id"
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.rows = ROWS
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, self.rows, "")

    def observe(self):
        return mod.snapshot(boot_path=self.boot, runner=self.runner, now=NOW)

    def test_only_metadata_labels_are_requested_and_latest_revision_is_retained(self) -> None:
        result = self.observe()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["revision_count"], 3)
        self.assertEqual(len(result["releases"]), 2)
        loki = next(item for item in result["releases"] if item["name"] == "loki")
        self.assertEqual((loki["revision"], loki["status"]), (2, "deployed"))
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, mod.COMMAND)
        self.assertIn("custom-columns=NS:", " ".join(argv))
        self.assertNotIn(".data", " ".join(argv))
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("release payload", json.dumps(result))

    def test_pending_is_an_observation_not_a_health_pass(self) -> None:
        self.rows = "observability-stg loki deployed 1\nobservability-stg loki pending-upgrade 2\n"
        result = self.observe()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["releases"][0]["status"], "pending-upgrade")

    def test_invalid_extra_secret_duplicate_and_oversize_are_unknown(self) -> None:
        for rows in ("observability-stg loki deployed 1 SECRET\n",
                     "observability-stg loki deployed 1\nobservability-stg loki failed 1\n",
                     "observability-stg loki deployed 001\n",
                     "bad/namespace loki deployed 1\n",
                     "observability-stg loki deployed 1\n" * 129):
            with self.subTest(rows=rows[:20]):
                self.rows = rows
                value = self.observe()
                self.assertEqual(value["status"], "unknown")
                self.assertNotIn("SECRET", json.dumps(value))

    def test_missing_boot_or_failed_kubectl_is_unknown(self) -> None:
        self.boot.unlink()
        self.assertEqual(self.observe()["status"], "unknown")
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        def failed(argv, **_):
            return subprocess.CompletedProcess(argv, 1, "", "SECRET")
        value = mod.snapshot(boot_path=self.boot, runner=failed, now=NOW)
        self.assertEqual(value["status"], "unknown")
        self.assertNotIn("SECRET", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
