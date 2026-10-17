"""Source-only fixtures for fixed staging post-pull receipt reader."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name("pxe_post_pull_receipt_status.py")
SPEC = importlib.util.spec_from_file_location("post_pull_receipt_status_fixture", FILE)
assert SPEC is not None and SPEC.loader is not None
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)

NOW = 100000.0
BOOT = "123e4567-e89b-12d3-a456-426614174000"
SHA = "a" * 40


class RoleReceiptStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = self.root / "boot"
        self.uptime = self.root / "uptime"
        self.states = self.root / "ansible-pull"
        self.states.mkdir()
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.uptime.write_text("100.0 1000.0\n", encoding="ascii")
        self.role = "k3s_stg_agent"
        self.receipt = self.states / (self.role + ".post-pull.env")
        self.state = self.states / (self.role + ".env")
        self.good()

    def write(self, path, text, mtime):
        path.write_text(text, encoding="ascii")
        path.chmod(0o600)
        os.utime(path, (mtime, mtime))

    def good(self, role=None):
        role = self.role if role is None else role
        receipt = self.states / (role + ".post-pull.env")
        state = self.states / (role + ".env")
        self.write(receipt, "\n".join((
            "SCHEMA=1", "ROLE=" + role, "STAGE=stg", "BRANCH=stg",
            "BOOT_ID=" + BOOT, "CHECKOUT_HEAD_AFTER_SUCCESS=" + SHA,
            "RECORDED_AT_EPOCH=99945", "",
        )), 99945)
        self.write(state, "\n".join((
            "ROLE=" + role, "STAGE=stg", "BRANCH=stg",
            "RC=0", "ELAPSED_SECS=42", "STEP=complete", "",
        )), 99944)

    def snap(self):
        return reader.snapshot(
            boot_path=self.boot, uptime_path=self.uptime,
            state_dir=self.states, now_fn=lambda: NOW,
        )

    def one(self):
        return next(r for r in self.snap()["roles"] if r["role"] == self.role)

    def edit(self, path, before, after, *, time_value):
        self.write(path, path.read_text().replace(before, after), time_value)

    def test_valid_receipt_is_historical_partial_not_execution_proof(self):
        report = self.snap()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["scope"], "historical_post_success_checkout_not_executed_revision")
        self.assertEqual(report["boot_id"], BOOT)
        self.assertEqual([r["role"] for r in report["roles"]], list(reader.ROLES))
        self.assertEqual(report["observed_at"], "1970-01-02T03:46:40Z")
        role = self.one()
        self.assertEqual(role["status"], "observed")
        self.assertEqual(role["checkout_head_after_success"], SHA)
        self.assertEqual(role["recorded_at"], "1970-01-02T03:45:45Z")
        self.assertNotIn("executed_ansible_pull_revision", json.dumps(report))
        self.assertNotIn("pass", json.dumps(report))

    def test_missing_roles_are_unknown_and_do_not_spoof_success(self):
        for role in self.snap()["roles"]:
            if role["role"] != self.role:
                self.assertEqual(role["status"], "unknown")
                self.assertEqual(role["reason"], "receipt_missing")
        self.receipt.unlink()
        report = self.snap()
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["reason"], "no_valid_post_pull_receipts")

    def test_distinct_fixed_roles_are_preserved(self):
        self.good("k3s_stg_server")
        report = self.snap()
        statuses = {r["role"]: r["status"] for r in report["roles"]}
        self.assertEqual(statuses["k3s_stg_server"], "observed")
        self.assertEqual(statuses["k3s_stg_agent"], "observed")
        self.assertEqual(len(report["roles"]), 6)

    def test_old_success_is_rejected_after_failed_role_attempt(self):
        self.edit(self.state, "RC=0", "RC=1", time_value=99947)
        self.assertEqual(self.one()["reason"], "receipt_identity_or_status_mismatch")

    def test_old_boot_receipt_rejected(self):
        self.edit(self.receipt, "BOOT_ID=" + BOOT,
                  "BOOT_ID=ffffffff-e89b-12d3-a456-426614174000", time_value=99945)
        self.assertEqual(self.one()["status"], "unknown")

    def test_receipt_with_previous_boot_mtime_rejected(self):
        os.utime(self.receipt, (99800, 99800))
        self.assertEqual(self.one()["reason"], "receipt_time_invalid")

    def test_post_pull_timestamp_mismatch_rejected(self):
        self.edit(self.receipt, "RECORDED_AT_EPOCH=99945",
                  "RECORDED_AT_EPOCH=99900", time_value=99945)
        self.assertEqual(self.one()["reason"], "receipt_time_invalid")

    def test_future_receipt_and_role_state_rejected(self):
        self.edit(self.receipt, "RECORDED_AT_EPOCH=99945",
                  "RECORDED_AT_EPOCH=100050", time_value=100050)
        self.assertEqual(self.one()["status"], "unknown")
        self.good()
        os.utime(self.state, (99970, 99970))
        self.assertEqual(self.one()["status"], "unknown")

    def test_prod_or_wrong_branch_rejected(self):
        self.edit(self.receipt, "STAGE=stg", "STAGE=prod", time_value=99945)
        self.assertEqual(self.one()["status"], "unknown")
        self.good()
        self.edit(self.state, "BRANCH=stg", "BRANCH=main", time_value=99944)
        self.assertEqual(self.one()["status"], "unknown")

    def test_untrusted_extra_key_does_not_leak(self):
        self.write(self.receipt, self.receipt.read_text() + "TOKEN=secret-data\n", 99945)
        report = self.snap()
        self.assertEqual(self.one()["reason"], "receipt_invalid")
        self.assertNotIn("secret-data", json.dumps(report))

    def test_duplicate_keys_and_invalid_sha_rejected(self):
        self.write(self.receipt, self.receipt.read_text() + "ROLE=another\n", 99945)
        self.assertEqual(self.one()["reason"], "receipt_invalid")
        self.good()
        self.edit(self.receipt, SHA, "main", time_value=99945)
        self.assertEqual(self.one()["reason"], "receipt_identity_or_status_mismatch")

    def test_symlink_and_world_writable_receipts_rejected(self):
        moved = self.root / "source"
        self.receipt.rename(moved)
        self.receipt.symlink_to(moved)
        self.assertEqual(self.one()["status"], "unknown")
        self.receipt.unlink()
        moved.rename(self.receipt)
        self.receipt.chmod(0o666)
        self.assertEqual(self.one()["reason"], "receipt_invalid")

    def test_missing_or_corrupt_role_state_rejected(self):
        self.state.unlink()
        self.assertEqual(self.one()["reason"], "role_state_receipt_missing")
        self.good()
        self.write(self.state, "ROLE=k3s_stg_agent\n", 99944)
        self.assertEqual(self.one()["reason"], "role_state_receipt_invalid")

    def test_boot_changes_while_reading_discard_all_receipts(self):
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=[
            BOOT + "\n", "100.0 1000.0\n",
            "ffffffff-e89b-12d3-a456-426614174000\n",
        ]):
            report = self.snap()
        self.assertEqual(report["reason"], "boot_identity_changed")
        self.assertEqual(report["roles"], [])

    def test_invalid_uptime_and_nan_fail_closed(self):
        self.uptime.write_text("nan\n")
        self.assertEqual(self.snap()["status"], "unknown")
        self.uptime.write_text("100.0\n")
        value = reader.snapshot(
            boot_path=self.boot, uptime_path=self.uptime, state_dir=self.states,
            now_fn=lambda: float("inf"),
        )
        self.assertEqual(value["status"], "unknown")

    def test_cli_refuses_arbitrary_path_or_role(self):
        for args in (["--json", "--role", "base"], ["--json", "--path", "/tmp/foo"]):
            with self.assertRaises(SystemExit) as caught:
                reader.main(args)
            self.assertEqual(caught.exception.code, 2)

    def test_reader_has_no_network_or_git_subprocess(self):
        self.assertFalse(hasattr(reader, "subprocess"))
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
