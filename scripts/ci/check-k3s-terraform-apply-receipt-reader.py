#!/usr/bin/env python3
"""Synthetic current-boot Terraform receipt reader checks."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "k3s_terraform_apply_receipt_status.py"
spec = importlib.util.spec_from_file_location("terraform_apply_receipt_tested", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

NOW = 1_800_000_000
BOOT = "11111111-2222-3333-4444-555555555555"


class ReceiptReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.receipt = root / "staging.done"
        self.boot = root / "boot_id"
        self.uptime = root / "uptime"
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.uptime.write_text("100.00 100.00\n", encoding="ascii")
        self.fields = {
            "SCHEMA": mod.RECEIPT_SCHEMA,
            "SCOPE": mod.SCOPE,
            "BOOT_ID": BOOT,
            "BUNDLE_ID": "a" * 12 + "-" + "b" * 12,
            "INFRA_REVISION": "a" * 40,
            "INVENTORY_REVISION": "b" * 40,
            "APPLIED_AT_EPOCH": str(NOW - 1),
        }
        self.write()

    def write(self) -> None:
        self.receipt.write_text("".join(f"{key}={self.fields[key]}\n" for key in mod.FIELDS),
                                encoding="ascii")
        self.receipt.chmod(0o600)
        os.utime(self.receipt, (NOW, NOW))

    def observe(self, **kwargs) -> dict:
        return mod.snapshot(receipt_path=self.receipt, boot_path=self.boot,
                            uptime_path=self.uptime, now_fn=lambda: NOW,
                            expected_uid=os.getuid(), **kwargs)

    def test_valid_receipt_is_partial_with_bounded_identity(self) -> None:
        value = self.observe()
        self.assertEqual(value["status"], "partial")
        self.assertEqual(value["bundle_id"], self.fields["BUNDLE_ID"])
        self.assertEqual(value["infra_revision"], self.fields["INFRA_REVISION"])
        self.assertEqual(value["inventory_revision"], self.fields["INVENTORY_REVISION"])
        self.assertRegex(value["receipt_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("not_helm_state_attested", value["scope"])

    def test_identity_drift_and_extra_field_are_unknown_without_echo(self) -> None:
        for key, bad in (("BOOT_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                         ("BUNDLE_ID", "c" * 12 + "-" + "b" * 12),
                         ("INFRA_REVISION", "A" * 40)):
            with self.subTest(key=key):
                original = self.fields[key]
                self.fields[key] = bad
                self.write()
                self.assertEqual(self.observe()["status"], "unknown")
                self.fields[key] = original
        self.write()
        with self.receipt.open("a", encoding="ascii") as stream:
            stream.write("TOKEN=SECRET\n")
        self.assertNotIn("SECRET", json.dumps(self.observe()))
        self.write()
        self.receipt.write_bytes(self.receipt.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.observe()["status"], "unknown")

    def test_symlink_mode_and_owner_are_unknown(self) -> None:
        self.receipt.chmod(0o644)
        self.assertEqual(self.observe()["status"], "unknown")
        self.write()
        self.assertEqual(mod.snapshot(receipt_path=self.receipt, boot_path=self.boot,
                                      uptime_path=self.uptime, now_fn=lambda: NOW,
                                      expected_uid=os.getuid() + 1)["status"], "unknown")
        target = Path(self.tmp.name) / "target"
        self.receipt.rename(target)
        self.receipt.symlink_to(target)
        self.assertEqual(self.observe()["status"], "unknown")

    def test_old_or_future_time_is_unknown(self) -> None:
        for epoch in (NOW - 110, NOW + 100):
            self.fields["APPLIED_AT_EPOCH"] = str(epoch)
            self.write()
            self.assertEqual(self.observe()["reason"], "receipt_time_invalid")

    def test_missing_receipt_and_boot_change_are_unknown(self) -> None:
        self.receipt.unlink()
        self.assertEqual(self.observe()["status"], "unknown")
        self.write()

        class ChangingBoot:
            reads = 0
            def read_text(self, **_):
                self.reads += 1
                return BOOT if self.reads == 1 else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        self.assertEqual(mod.snapshot(receipt_path=self.receipt, boot_path=ChangingBoot(),
                                      uptime_path=self.uptime, now_fn=lambda: NOW,
                                      expected_uid=os.getuid())["reason"],
                         "boot_identity_changed")


if __name__ == "__main__":
    unittest.main()
