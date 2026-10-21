"""Synthetic safety checks for the fixed pinned playbook receipt reader."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).with_name("pxe_pinned_execution_status.py")
SPEC = importlib.util.spec_from_file_location("pinned_execution_status_tested", SOURCE)
assert SPEC and SPEC.loader
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)

BOOT = "123e4567-e89b-12d3-a456-426614174000"
SHA = "a" * 40
DIGEST = "b" * 64
BUILD = "20261020T164511Z-stg-generation-522351"


class PinnedExecutionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "ansible-pull"
        self.state.mkdir()
        self.boot = self.root / "boot"
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.uptime = self.root / "uptime"
        self.uptime.write_text("100.0 1000.0\n", encoding="ascii")
        self.receipt = self.state / "base.pinned-execution.env"
        self.role_state = self.state / "base.env"
        self.write_good()

    def put(self, path, contents, mtime):
        path.write_text(contents, encoding="ascii")
        path.chmod(0o600)
        os.utime(path, (mtime, mtime))

    def write_good(self):
        self.put(self.receipt, "\n".join((
            "SCHEMA=1", "EVIDENCE_SCOPE=" + reader.SCOPE,
            "ROLE=base", "STAGE=stg", "BRANCH=stg", "BOOT_ID=" + BOOT,
            "BUILD_ID=" + BUILD, "PIN_REVISION=" + SHA,
            "DECLARATION_SHA256=" + DIGEST,
            "CHECKOUT_HEAD_AFTER_SUCCESS=" + SHA,
            "RECORDED_AT_EPOCH=99945", "",
        )), 99945)
        self.put(self.role_state, "\n".join((
            "ROLE=base", "STAGE=stg", "BRANCH=stg", "RC=0",
            "ELAPSED_SECS=42", "STEP=complete", "",
        )), 99944)

    def snap(self):
        return reader.snapshot(
            boot_path=self.boot, uptime_path=self.uptime,
            state_dir=self.state, now_fn=lambda: 100000.0,
        )

    def base(self):
        return next(item for item in self.snap()["roles"] if item["role"] == "base")

    def replace(self, old, new):
        self.put(self.receipt, self.receipt.read_text().replace(old, new), 99945)

    def test_success_is_partial_playbook_evidence_with_fixed_roles(self):
        report = self.snap()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["scope"], reader.SCOPE)
        self.assertEqual([item["role"] for item in report["roles"]], list(reader.ROLES))
        self.assertEqual(self.base()["pin_revision"], SHA)
        self.assertEqual(self.base()["build_id"], BUILD)
        self.assertEqual(self.base()["declaration_sha256"], DIGEST)
        self.assertNotIn('"executed_role_revision"', json.dumps(report))

    def test_missing_or_legacy_receipt_is_unknown(self):
        self.receipt.rename(self.state / "base.post-pull.env")
        self.assertEqual(self.snap()["status"], "unknown")
        self.assertEqual(self.base()["reason"], "receipt_missing")

    def test_changed_identity_and_failed_role_state_are_unknown(self):
        for old, new in (("PIN_REVISION=" + SHA, "PIN_REVISION=" + "c" * 40),
                         ("BUILD_ID=" + BUILD, "BUILD_ID=../escape"),
                         ("DECLARATION_SHA256=" + DIGEST, "DECLARATION_SHA256=bad"),
                         ("EVIDENCE_SCOPE=" + reader.SCOPE, "EVIDENCE_SCOPE=untrusted"),
                         ("BOOT_ID=" + BOOT, "BOOT_ID=" + "f" * 36)):
            with self.subTest(old=old):
                self.write_good()
                self.replace(old, new)
                self.assertEqual(self.base()["status"], "unknown")
        self.write_good()
        self.put(self.role_state, self.role_state.read_text().replace("RC=0", "RC=1"), 99944)
        self.assertEqual(self.base()["status"], "unknown")

    def test_stale_symlink_and_extra_fields_do_not_leak(self):
        os.utime(self.receipt, (99800, 99800))
        self.assertEqual(self.base()["reason"], "receipt_time_invalid")
        self.write_good()
        moved = self.root / "moved"
        self.receipt.rename(moved)
        self.receipt.symlink_to(moved)
        self.assertEqual(self.base()["status"], "unknown")
        self.receipt.unlink()
        self.put(self.receipt, moved.read_text() + "TOKEN=hidden\n", 99945)
        self.assertEqual(self.base()["reason"], "receipt_invalid")
        self.assertNotIn("hidden", json.dumps(self.snap()))
        self.write_good()
        self.receipt.chmod(0o644)
        self.assertEqual(self.base()["reason"], "receipt_invalid")

    def test_cli_accepts_no_custom_target(self):
        for args in (["--json", "--role", "base"], ["--json", "--path", "/tmp/x"]):
            with self.assertRaises(SystemExit) as caught:
                reader.main(args)
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
