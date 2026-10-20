#!/usr/bin/env python3
"""Synthetic label-only Helm metadata probe checks."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
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


def item(namespace, name, status, revision):
    return {"apiVersion": "meta.k8s.io/v1", "kind": "PartialObjectMetadata",
            "metadata": {"namespace": namespace,
                         "labels": {"owner": "helm", "name": name,
                                    "status": status, "version": revision}}}


def response(items):
    return {"apiVersion": "meta.k8s.io/v1", "kind": "PartialObjectMetadataList",
            "metadata": {"continue": ""}, "items": items}


class HelmMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.boot = Path(self.tmp.name) / "boot_id"
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.value = response([
            item("observability-stg", "loki", "superseded", "1"),
            item("observability-stg", "loki", "deployed", "2"),
            item("arc-systems", "arc", "deployed", "1"),
        ])

    def fetcher(self):
        return self.value

    def observe(self):
        return mod.snapshot(boot_path=self.boot, fetcher=self.fetcher, now=NOW)

    def test_metadata_only_list_keeps_latest_revision(self) -> None:
        result = self.observe()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["revision_count"], 3)
        self.assertEqual(len(result["releases"]), 2)
        loki = next(item for item in result["releases"] if item["name"] == "loki")
        self.assertEqual((loki["revision"], loki["status"]), (2, "deployed"))
        self.assertNotIn("release payload", json.dumps(result))

    def test_api_requests_only_partial_metadata_without_full_object_fallback(self) -> None:
        calls = []
        class FakeResponse:
            status = 200
            def read(self, _):
                return json.dumps(self_value).encode("ascii")
        class FakeConnection:
            def request(self, method, path, *, headers):
                calls.append((method, path, headers))
            def getresponse(self):
                return FakeResponse()
            def close(self):
                pass
        self_value = self.value
        fetched = mod._fetch_metadata(
            connection_factory=lambda host, port, **kwargs: FakeConnection(),
            context_factory=lambda: object())
        self.assertEqual(fetched["kind"], "PartialObjectMetadataList")
        self.assertEqual(calls, [("GET", mod.API_PATH, {"Accept": mod.ACCEPT})])
        self.assertNotIn(",application/json", mod.ACCEPT)
        self_value = {**self.value, "kind": "SecretList"}
        with self.assertRaises(ValueError):
            mod._fetch_metadata(
                connection_factory=lambda host, port, **kwargs: FakeConnection(),
                context_factory=lambda: object())

    def test_pending_is_an_observation_not_a_health_pass(self) -> None:
        self.value = response([item("observability-stg", "loki", "deployed", "1"),
                               item("observability-stg", "loki", "pending-upgrade", "2")])
        result = self.observe()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["releases"][0]["status"], "pending-upgrade")

    def test_invalid_extra_secret_duplicate_and_oversize_are_unknown(self) -> None:
        bad_secret = item("observability-stg", "loki", "deployed", "1")
        bad_secret["data"] = "SECRET"
        for items in ([bad_secret],
                      [item("observability-stg", "loki", "deployed", "1"),
                       item("observability-stg", "loki", "failed", "1")],
                      [item("observability-stg", "loki", "deployed", "001")],
                      [item("bad/namespace", "loki", "deployed", "1")],
                      [item("observability-stg", "loki", "deployed", str(i + 1))
                       for i in range(129)]):
            with self.subTest(count=len(items)):
                self.value = response(items)
                value = self.observe()
                self.assertEqual(value["status"], "unknown")
                self.assertNotIn("SECRET", json.dumps(value))

    def test_missing_boot_or_failed_metadata_fetch_is_unknown(self) -> None:
        self.boot.unlink()
        self.assertEqual(self.observe()["status"], "unknown")
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        def failed():
            raise OSError("SECRET")
        value = mod.snapshot(boot_path=self.boot, fetcher=failed, now=NOW)
        self.assertEqual(value["status"], "unknown")
        self.assertNotIn("SECRET", json.dumps(value))

    def test_kubeconfig_symlink_is_not_followed(self) -> None:
        target = Path(self.tmp.name) / "target"
        target.write_text("SECRET", encoding="ascii")
        link = Path(self.tmp.name) / "kubeconfig"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            mod._tls_context(link)


if __name__ == "__main__":
    unittest.main()
