"""Source-only fixtures for the fixed read-only PXE node snapshot."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).with_name("pxe_node_lineage_status.py")
SPEC = importlib.util.spec_from_file_location("pxe_node_lineage_status_tested", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
ID = "123e4567-e89b-12d3-a456-426614174000"
NOW = 100000.0


class NodeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = self.root / "boot_id"
        self.uptime = self.root / "uptime"
        self.state = self.root / "last.env"
        self.repo = self.root / "checkout"
        (self.repo / ".git").mkdir(parents=True)
        self.boot.write_text(ID + "\n", encoding="ascii")
        self.uptime.write_text("100.0 1000.0\n", encoding="ascii")
        self.state.write_text(
            "ROLE=k3s_stg_agent\nSTAGE=stg\nBRANCH=stg\nRC=0\nELAPSED_SECS=42\nSTEP=complete\n",
            encoding="ascii",
        )
        os.utime(self.state, (NOW - 50, NOW - 50))
        self.git_result = subprocess.CompletedProcess([], 0, "a" * 40 + "\n", "")
        self.calls = []

    def runner(self, args):
        self.calls.append(args)
        return self.git_result

    def inspect(self, **changes):
        args = dict(boot_path=self.boot, uptime_path=self.uptime,
                    state_path=self.state, checkout_path=self.repo,
                    now_fn=lambda: NOW, runner=self.runner)
        args.update(changes)
        return probe.snapshot(**args)

    def test_complete_status_is_partial_not_acceptance(self):
        out = self.inspect()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["boot_id"], ID)
        self.assertEqual(out["checkout_head"], "a" * 40)
        self.assertEqual(out["ansible_pull_state"], "completed")
        self.assertIsNone(out["executed_ansible_pull_revision"])
        self.assertIsNone(out["boot_selector"])
        self.assertEqual(out["observed_at"], "1970-01-02T03:46:40Z")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][-2:], ("--verify", "HEAD^{commit}"))

    def test_state_success_not_proof_of_executed_commit(self):
        out = self.inspect()
        self.assertEqual(out["reason"], "checkout_head_observed_not_execution_attested")
        self.assertNotIn("accepted", json.dumps(out))
        self.assertNotIn("token", json.dumps(out))

    def test_state_not_completed_preserved(self):
        content = self.state.read_text().replace("RC=0", "RC=1").replace("STEP=complete", "STEP=ansible_pull")
        self.state.write_text(content)
        os.utime(self.state, (NOW - 50, NOW - 50))
        self.assertEqual(self.inspect()["ansible_pull_state"], "not_completed")
        self.assertNotEqual(self.inspect()["reason"], "accepted")

    def test_stale_state_not_previous_boot_evidence(self):
        os.utime(self.state, (NOW - 200, NOW - 200))
        value = self.inspect()
        self.assertEqual(value["status"], "unknown")
        self.assertEqual(value["reason"], "ansible_pull_state_unavailable")

    def test_invalid_or_extra_state_key_rejected_without_echo(self):
        with self.state.open("a") as handle:
            handle.write("TOKEN=secret-value\n")
        os.utime(self.state, (NOW - 50, NOW - 50))
        value = self.inspect()
        self.assertEqual(value["status"], "unknown")
        self.assertNotIn("secret-value", json.dumps(value))

    def test_symlink_state_rejected(self):
        other = self.root / "other"
        other.write_text(self.state.read_text())
        self.state.unlink()
        self.state.symlink_to(other)
        self.assertEqual(self.inspect()["reason"], "ansible_pull_state_unavailable")

    def test_missing_boot_and_malformed_id(self):
        self.boot.unlink()
        self.assertEqual(self.inspect()["reason"], "boot_identity_unavailable")
        self.boot.write_text("not-a-boot\n")
        self.assertEqual(self.inspect()["reason"], "boot_identity_invalid")

    def test_boot_changed_during_inspection(self):
        with (
            mock.patch.object(probe, "_state", return_value="completed"),
            mock.patch.object(Path, "read_text", autospec=True, side_effect=[
                ID + "\n", "100.0 1000.0\n", "ffffffff-e89b-12d3-a456-426614174000\n",
            ]),
        ):
            self.assertEqual(self.inspect()["reason"], "boot_identity_changed")

    def test_git_missing_or_invalid_remains_unknown(self):
        self.git_result = subprocess.CompletedProcess([], 1, "", "")
        self.assertEqual(self.inspect()["reason"], "checkout_head_unavailable")
        self.git_result = subprocess.CompletedProcess([], 0, "main\n", "")
        self.assertEqual(self.inspect()["reason"], "checkout_head_unavailable")

    def test_symlink_checkout_not_supported(self):
        original = self.repo
        moved = self.root / "moved"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.inspect()["reason"], "checkout_head_unavailable")

    def test_wrong_stage_does_not_pass(self):
        self.state.write_text(self.state.read_text().replace("STAGE=stg", "STAGE=prod"))
        os.utime(self.state, (NOW - 50, NOW - 50))
        self.assertEqual(self.inspect()["status"], "unknown")

    def test_no_ssh_host_or_runtime_mutation(self):
        self.inspect()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "git")
        self.assertNotIn("ssh", str(self.calls))
        self.assertNotIn("fetch", str(self.calls))
        self.assertNotIn("reset", str(self.calls))

    def test_cli_rejects_arbitrary_paths(self):
        with self.assertRaises(SystemExit) as raised:
            probe.main(["--json", "--state-path", "other"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
