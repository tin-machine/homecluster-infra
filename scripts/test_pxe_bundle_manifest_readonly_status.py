"""Source-only tests for paired PXE bundle manifest digest probe."""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("pxe_bundle_manifest_readonly_status.py")
SPEC = importlib.util.spec_from_file_location("pxe_manifest_status_fixture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
DATE = "20261017"


def manifest():
    return {
        "schema": 1,
        "policy": "paired_clean",
        "bundle_date": DATE,
        "releases": {"rpi4": DATE + "-rpi4", "rpi5": DATE + "-rpi5"},
        "target_hosts": ["private-example-host"],
        "target_stages": ["stg"],
        "required_tftp_paths": [
            {"generation": "rpi4", "release": DATE + "-rpi4", "path": "kernel8.img"},
            {"generation": "rpi5", "release": DATE + "-rpi5", "path": "kernel8.img"},
        ],
        "markers": [
            {"artifact_type": "rootfs", "generation": "rpi4", "release": DATE + "-rpi4",
             "marker_path": "/srv/gentoo/private-marker"},
        ],
        "metadata": {"internal_host": "redacted-only-digest"},
    }


class BundleManifestStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / (DATE + ".json")
        self.document = manifest()
        self.write()

    def write(self):
        self.path.write_text(
            json.dumps(self.document, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )

    def snapshot(self, date=DATE):
        return probe.inspect(bundle_date=date, root=self.root)

    def test_fixed_manifest_sha256_exact_raw_bytes_and_no_private_echo(self):
        result = self.snapshot()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "manifest_bytes_observed_run_binding_unknown")
        self.assertEqual(result["scope"], "paired_manifest_bytes_unbound_to_generation_run")
        self.assertEqual(
            result["manifest_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest(),
        )
        self.assertEqual(result["releases"], {
            "rpi4": DATE + "-rpi4", "rpi5": DATE + "-rpi5",
        })
        self.assertIsNone(result["generation_run_id"])
        self.assertIsNone(result["source_revision"])
        self.assertNotIn("private-example-host", json.dumps(result))
        self.assertNotIn("redacted-only-digest", json.dumps(result))

    def test_missing_file_and_symlink_fail_closed(self):
        self.path.unlink()
        self.assertEqual(self.snapshot()["reason"], "manifest_missing")
        target = self.root / "target"
        target.write_text("{}")
        self.path.symlink_to(target)
        self.assertEqual(self.snapshot()["status"], "unknown")

    def test_invalid_date_refuses_paths(self):
        for val in ("../../tmp", "20261017.json", "20261301", "20260230", "令和0801", "2026-10-17"):
            with self.subTest(val=val):
                self.assertEqual(self.snapshot(val)["reason"], "bundle_date_invalid")

    def test_wrong_date_and_policy_fail(self):
        self.document["bundle_date"] = "20261018"
        self.write()
        self.assertEqual(self.snapshot()["reason"], "manifest_contract_invalid")
        self.document = manifest()
        self.document["policy"] = "shared_tftp"
        self.write()
        self.assertEqual(self.snapshot()["reason"], "manifest_contract_invalid")

    def test_schema_unknown_key_and_secret_not_echoed(self):
        self.document["token"] = "should-never-show"
        self.write()
        r = self.snapshot()
        self.assertEqual(r["status"], "unknown")
        self.assertNotIn("should-never-show", json.dumps(r))
        self.document = manifest()
        self.document["schema"] = True
        self.write()
        self.assertEqual(self.snapshot()["status"], "unknown")

    def test_release_swap_fails(self):
        self.document["releases"]["rpi4"] = DATE + "-rpi5"
        self.write()
        self.assertEqual(self.snapshot()["reason"], "manifest_contract_invalid")

    def test_tftp_paths_wrong_generation_or_release_fail(self):
        self.document["required_tftp_paths"][0]["release"] = DATE + "-rpi5"
        self.write()
        self.assertEqual(self.snapshot()["status"], "unknown")
        self.document = manifest()
        self.document["required_tftp_paths"][0]["generation"] = "other"
        self.write()
        self.assertEqual(self.snapshot()["status"], "unknown")

    def test_unhashable_nested_generation_does_not_raise_or_expose_private_data(self):
        self.document["required_tftp_paths"][0]["generation"] = ["rpi4"]
        self.write()
        self.assertEqual(self.snapshot()["reason"], "manifest_contract_invalid")
        self.document = manifest()
        self.document["markers"][0]["generation"] = {"untrusted": "secret"}
        self.write()
        result = self.snapshot()
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("secret", json.dumps(result))

    def test_markers_wrong_type_or_release_fail(self):
        self.document["markers"][0]["artifact_type"] = "write-action"
        self.write()
        self.assertEqual(self.snapshot()["status"], "unknown")
        self.document = manifest()
        self.document["markers"][0]["release"] = DATE + "-rpi5"
        self.write()
        self.assertEqual(self.snapshot()["status"], "unknown")

    def test_malformed_json_and_oversized_file_fail(self):
        self.path.write_text("{secret-value-not-json", encoding="utf-8")
        self.assertEqual(self.snapshot()["reason"], "manifest_invalid")
        self.path.write_text("x" * (probe.MAX_BYTES + 2), encoding="utf-8")
        self.assertEqual(self.snapshot()["reason"], "manifest_invalid")

    def test_digest_change_is_observable_without_attributing_run(self):
        before = self.snapshot()
        self.document["metadata"]["generation_note"] = "new-release"
        self.write()
        after = self.snapshot()
        self.assertNotEqual(before["manifest_sha256"], after["manifest_sha256"])
        self.assertIsNone(after["generation_run_id"])

    def test_unknown_root_path_and_no_arbitrary_cli(self):
        self.assertEqual(self.snapshot()["status"], "partial")
        with self.assertRaises(SystemExit) as caught:
            probe.main(["--json", "--bundle-date", DATE, "--root", "/tmp"])
        self.assertEqual(caught.exception.code, 2)

    def test_no_network_or_runner_mutations(self):
        self.assertFalse(hasattr(probe, "subprocess"))
        self.assertFalse(hasattr(probe, "ssh"))
        self.assertFalse(hasattr(probe, "ansible"))

    def test_output_keys_and_unknown_contract(self):
        expected = {
            "schema", "status", "reason", "scope", "bundle_date",
            "manifest_sha256", "releases", "generation_run_id", "source_revision",
        }
        self.assertEqual(set(self.snapshot()), expected)
        self.path.unlink()
        no = self.snapshot()
        self.assertEqual(set(no), expected)
        self.assertIsNone(no["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
