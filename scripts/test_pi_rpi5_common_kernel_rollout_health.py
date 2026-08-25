from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROLLOUT = HERE / "pi-rpi5-common-kernel-rollout"


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ROLLOUT_MODULE = load(ROLLOUT, "pi_rpi5_common_kernel_rollout_health_test")


class RolloutHealthTests(unittest.TestCase):
    def runbook(self, root: Path) -> Path:
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        helper = scripts / "pi-k3s-status"
        helper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return root

    def completed(self, status: str, returncode: int, **extra):
        payload = {
            "status": status,
            "reason": f"cluster_{status}",
            "nodes_ready": extra.get("nodes_ready", 1),
            "nodes_total": extra.get("nodes_total", 2),
            "issues": extra.get("issues", ["nodes_not_ready"]),
        }
        return subprocess.CompletedProcess(["fixture"], returncode, json.dumps(payload), "")

    def test_cluster_health_is_observation_not_rollout_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            with mock.patch.object(
                ROLLOUT_MODULE.legacy,
                "run",
                return_value=self.completed("blocked", 1, nodes_ready=3, nodes_total=4, issues=["node_pressure"]),
            ):
                ROLLOUT_MODULE.observe_cluster_health(runbook)
            self.assertEqual(ROLLOUT_MODULE._PRE_ROLLOUT_CLUSTER_STATUS, "blocked")
            self.assertEqual(ROLLOUT_MODULE._PRE_ROLLOUT_CLUSTER_ISSUES, "node_pressure")

    def test_cluster_health_invalid_json_remains_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            completed = subprocess.CompletedProcess(["fixture"], 2, "not-json", "")
            with mock.patch.object(ROLLOUT_MODULE.legacy, "run", return_value=completed):
                ROLLOUT_MODULE.observe_cluster_health(runbook)
            self.assertEqual(ROLLOUT_MODULE._PRE_ROLLOUT_CLUSTER_STATUS, "unknown")
            self.assertEqual(ROLLOUT_MODULE._PRE_ROLLOUT_CLUSTER_ISSUES, "invalid_status_json")

    def record(self, *, previous: str, applied: str, fresh_boot: str, kernel: bool | None):
        ROLLOUT_MODULE._CURRENT_ACTION = "generic_canary"
        ROLLOUT_MODULE._LAST_FRESH_BOOT_STATUS = fresh_boot
        ROLLOUT_MODULE._LAST_KERNEL_ACCEPTANCE = kernel
        return ROLLOUT_MODULE.phase_record_value(
            phase="generic_canary",
            targets=["generic-a"],
            selector_result={
                "previous_selector_by_node": {
                    "generic-a": {"tftp_release": previous, "rootfs_release": previous}
                },
                "applied_selector_by_node": {
                    "generic-a": {"tftp_release": applied, "rootfs_release": applied}
                },
            },
            accepted={
                "generation_run_id": "20260822T000000Z-stg-generation-1",
                "exact_kernel_release": "6.18.36-v8-homecluster+",
            },
            fresh_boot_run_id="20260826T000000Z",
            acceptance_status="fail",
            started_at="2026-08-26T00:00:00Z",
        )

    def test_reboot_failure_is_not_kernel_failure_or_rollback_candidate(self):
        record = self.record(
            previous="20260820-rpi5",
            applied="20260822-rpi5",
            fresh_boot="fail",
            kernel=None,
        )
        self.assertEqual(record["acceptance_status"], "reboot_fail")
        self.assertEqual(record["reboot_acceptance_status"], "fail")
        self.assertEqual(record["kernel_acceptance_status"], "not_run")
        self.assertFalse(record["rollback_recommended"])

    def test_kernel_failure_with_no_selector_change_does_not_recommend_rollback(self):
        record = self.record(
            previous="20260820-rpi5",
            applied="20260820-rpi5",
            fresh_boot="pass",
            kernel=False,
        )
        self.assertEqual(record["acceptance_status"], "fail")
        self.assertEqual(record["kernel_acceptance_status"], "fail")
        self.assertFalse(record["selector_changed"])
        self.assertFalse(record["rollback_recommended"])

    def test_kernel_failure_with_selector_change_can_recommend_rollback(self):
        record = self.record(
            previous="20260820-rpi5",
            applied="20260822-rpi5",
            fresh_boot="pass",
            kernel=False,
        )
        self.assertTrue(record["selector_changed"])
        self.assertTrue(record["rollback_recommended"])

    def test_phase_acceptance_keeps_cluster_health_as_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runbook = root / "runbook"
            (runbook / "scripts").mkdir(parents=True)
            (runbook / "scripts/pi-k3s-status").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            inventory = root / "inventory.yml"
            inventory.write_text("all: {}\n", encoding="utf-8")

            calls = []

            def fake_run(command, *, cwd, timeout, env=None):
                calls.append([str(item) for item in command])
                if str(command[0]).endswith("ansible-playbook"):
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(
                    command,
                    1,
                    json.dumps({"status": "blocked", "issues": ["non_running_pods"]}),
                    "",
                )

            with mock.patch.object(ROLLOUT_MODULE.legacy, "run", side_effect=fake_run):
                accepted, diagnostics = ROLLOUT_MODULE.run_phase_acceptance(
                    root,
                    runbook,
                    inventory,
                    "generic_canary",
                    ["generic-a"],
                    ["generic-a", "egpu-a"],
                    "6.18.36-v8-homecluster+",
                )
            self.assertTrue(accepted)
            self.assertIn("phase_runtime_acceptance=pass", diagnostics)
            self.assertIn("k3s_final_observation=blocked", diagnostics)
            self.assertIn("k3s_final_warning_issues=non_running_pods", diagnostics)

    def test_ansible_failure_diagnostics_parse_modern_error(self):
        output = """
TASK [Pi5 common kernel target selector存在を検証] ********
task path: /repo/ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml:50
[ERROR]: Task failed: Action failed: rollout targetの現在selectorを一意に解決できません
Origin: /repo/ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml:50:7
failed: [router-a] (item=(censored due to no_log)) => {"censored":"hidden","changed":false}
"""
        diagnostics = ROLLOUT_MODULE.ansible_failure_diagnostics(
            output,
            stage="pre_mutation_selector_validation",
            mutation_committed=False,
            power_cycle_started=False,
            next_check_id="common_kernel_selector_apply",
        )
        self.assertIn("ansible_failed_task=Pi5 common kernel target selector存在を検証", diagnostics)
        self.assertIn("ansible_failed_host=router-a", diagnostics)
        self.assertIn("runtime_mutation_committed=false", diagnostics)
        self.assertIn("power_cycle_started=false", diagnostics)

    def test_ansible_failure_diagnostics_parse_classic_fatal_json(self):
        output = """
TASK [Run command] ***
fatal: [node-a]: FAILED! => {"changed": false, "msg": "command failed", "rc": 7}
"""
        diagnostics = ROLLOUT_MODULE.ansible_failure_diagnostics(
            output,
            stage="phase_acceptance_after_power_cycle",
            mutation_committed=True,
            power_cycle_started=True,
            next_check_id="common_kernel_phase_acceptance",
        )
        self.assertIn("ansible_failed_task=Run command", diagnostics)
        self.assertIn("ansible_failed_host=node-a", diagnostics)
        self.assertIn("ansible_error=command failed", diagnostics)
        self.assertIn("ansible_failed_rc=7", diagnostics)


if __name__ == "__main__":
    unittest.main()
