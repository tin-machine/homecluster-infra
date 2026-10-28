from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def accepted_record(self, root: Path):
        run_id = "20261001T120000Z-common-kernel"
        accepted = root / "accepted-generation"
        accepted.mkdir(parents=True)
        path = accepted / f"{run_id}.json"
        path.write_text(
            json.dumps(
                {
                    "generation_run_id": run_id,
                    "exact_kernel_release": "6.18.40-v8-homecluster+",
                    "pxe_candidate_selector": "20261003-rpi5",
                    "gate_version": "rpi5-common-kernel-gate-v2",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return run_id, path, ROLLOUT_MODULE.sha256_file(path)

    def test_expected_accepted_generation_matches_exact_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id, path, digest = self.accepted_record(root)
            with mock.patch.dict(
                ROLLOUT_MODULE.os.environ,
                {
                    ROLLOUT_MODULE.EXPECTED_ACCEPTED_RUN_ENV: run_id,
                    ROLLOUT_MODULE.EXPECTED_ACCEPTED_SHA_ENV: digest,
                },
                clear=False,
            ):
                value = ROLLOUT_MODULE.expected_accepted_generation(root)

        self.assertEqual(
            value,
            {
                "run_id": run_id,
                "sha256": digest,
                "path": str(path.resolve()),
            },
        )

    def test_expected_accepted_generation_rejects_content_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id, path, digest = self.accepted_record(root)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["checked_at"] = "2026-10-02T00:00:00Z"
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            with mock.patch.dict(
                ROLLOUT_MODULE.os.environ,
                {
                    ROLLOUT_MODULE.EXPECTED_ACCEPTED_RUN_ENV: run_id,
                    ROLLOUT_MODULE.EXPECTED_ACCEPTED_SHA_ENV: digest,
                },
                clear=False,
            ):
                with self.assertRaises(ROLLOUT_MODULE.RolloutError) as raised:
                    ROLLOUT_MODULE.expected_accepted_generation(root)

        self.assertEqual(
            raised.exception.reason,
            "accepted_generation_expectation_changed",
        )
        self.assertEqual(raised.exception.status, "unknown")

    def run_main_to_health_gate(self, phase, health, *, helper_present=True):
        """Use the real CLI path; no subprocess or rollout mutation may escape."""
        mod = ROLLOUT_MODULE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runbook = self.runbook(root / "runbook") if helper_present else root / "missing"
            inventory = root / "inventory.yml"
            inventory.write_text("{}\n", encoding="utf-8")
            inventory.chmod(0o600)
            source = ROLLOUT.parent.parent

            def command(args, **kwargs):
                if args == ["git", "-C", source, "branch", "--show-current"]:
                    return subprocess.CompletedProcess(args, 0, "stg\n", "")
                if args == ["git", "-C", source, "status", "--porcelain"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args == ["bash", runbook / "scripts/pi-k3s-status", "--json"]:
                    self.assertEqual(kwargs, {"cwd": runbook, "timeout": 900})
                    return health
                raise AssertionError(f"unexpected command: {args}")

            output = io.StringIO()
            with (
                mock.patch.dict(mod.os.environ, {
                    "HOMECLUSTER_RPI5_COMMON_KERNEL_ROLLOUT_APPLY": "1",
                    "HOMECLUSTER_RUNBOOK_ROOT": str(runbook),
                    "HOMECLUSTER_ANSIBLE_INVENTORY": str(inventory),
                }),
                mock.patch.object(mod, "workflow_root", return_value=root / "state"),
                mock.patch.object(mod, "ProgressWriter"),
                mock.patch.object(mod, "expected_accepted_generation", return_value={"run_id": "fixture"}),
                mock.patch.object(mod, "latest_accepted", return_value={"generation_run_id": "fixture"}),
                mock.patch.object(mod, "inventory_targets", return_value=(["agent-a", "agent-b", "agent-c"], "agent-a")),
                mock.patch.object(mod, "inventory_json", return_value={
                    "k3s_stg_server": {"hosts": ["server"]},
                    "env_stg": {"hosts": ["server", "agent-a", "agent-b", "agent-c"]},
                    "env_prod": {"hosts": ["prod"]},
                }),
                mock.patch.object(mod, "target_policy", return_value=(["agent-a"], {}, phase)),
                mock.patch.object(mod, "run", side_effect=command) as run,
                mock.patch.object(mod, "ensure_no_active_fresh_boot",
                                  side_effect=mod.RolloutError("fixture_after_health")) as next_gate,
                mock.patch.object(mod, "run_fresh_boot", side_effect=AssertionError("power mutation")) as reboot,
                redirect_stdout(output),
            ):
                exit_code = mod.main(["--json", "--phase", phase])
            self.assertEqual(exit_code, 2)
            reboot.assert_not_called()
            self.assertEqual(run.call_count, 3 if helper_present else 2)
            return json.loads(output.getvalue()), next_gate.call_count

    def test_runtime_phases_stop_before_selector_on_unhealthy_cluster(self):
        for phase in ROLLOUT_MODULE.RUNTIME_PHASES:
            for state, status in (("blocked", "blocked"), ("converging", "blocked"), ("unknown", "unknown")):
                with self.subTest(phase=phase, state=state):
                    result, continued = self.run_main_to_health_gate(
                        phase, self.completed(state, 1, issues=["node_pressure"]))
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["reason"], f"rollout_cluster_{state}")
                    self.assertIn("k3s_issues=node_pressure", result["diagnostics"])
                    self.assertEqual(continued, 0)

    def test_healthy_cluster_reaches_next_gate(self):
        for phase in ROLLOUT_MODULE.RUNTIME_PHASES:
            with self.subTest(phase=phase):
                result, continued = self.run_main_to_health_gate(
                    phase, self.completed("healthy", 0, issues=[]))
                self.assertEqual(result["reason"], "fixture_after_health")
                self.assertEqual(continued, 1)

    def test_invalid_health_contract_cannot_reach_selector(self):
        for stdout in ("not-json", "[]", "null", "{}", '{"status":"unexpected"}'):
            with self.subTest(stdout=stdout):
                result, continued = self.run_main_to_health_gate(
                    "full_fleet", subprocess.CompletedProcess([], 0, stdout, ""))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["reason"], "rollout_k3s_status_contract_invalid")
                self.assertEqual(continued, 0)
        result, continued = self.run_main_to_health_gate("full_fleet", self.completed("healthy", 1))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "rollout_k3s_status_exit_mismatch")
        self.assertEqual(continued, 0)

    def test_missing_status_helper_blocks_before_selector(self):
        result, continued = self.run_main_to_health_gate("full_fleet", None, helper_present=False)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "runbook_k3s_status_helper_missing")
        self.assertEqual(continued, 0)

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
        # Keep legacy observation fields distinguishable from a passed gate.
        self.assertEqual(record["pre_rollout_cluster_status"], "not_checked")
        self.assertEqual(record["pre_rollout_cluster_issues"], "none")

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

    def test_phase_acceptance_waits_then_rejects_blocked_cluster(self):
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

            with mock.patch.object(ROLLOUT_MODULE, "run", side_effect=fake_run):
                with mock.patch.object(ROLLOUT_MODULE.time, "sleep", return_value=None):
                    accepted, diagnostics = ROLLOUT_MODULE.run_phase_acceptance(
                        root,
                        runbook,
                        inventory,
                        "generic_canary",
                        ["generic-a"],
                        ["generic-a", "egpu-a"],
                        "6.18.36-v8-homecluster+",
                    )
            self.assertFalse(accepted)
            self.assertIn("phase_runtime_acceptance=pass", diagnostics)
            self.assertIn("k3s_final_observation=blocked", diagnostics)
            self.assertIn("k3s_final_warning_issues=non_running_pods", diagnostics)
            self.assertIn("k3s_final_acceptance=fail", diagnostics)
            self.assertEqual(len(calls), 61)

    def test_phase_acceptance_waits_for_llm_after_cluster_is_healthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runbook = self.runbook(root / "runbook")
            (runbook / "scripts/pi-rpi5-egpu-llm-status").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            inventory = root / "inventory.yml"
            inventory.write_text("all: {}\n", encoding="utf-8")
            llm_attempts = 0

            def fake_run(command, *, cwd, timeout, env=None):
                nonlocal llm_attempts
                if str(command[0]).endswith("ansible-playbook"):
                    return subprocess.CompletedProcess(command, 0, "", "")
                if Path(command[1]).name == "pi-k3s-status":
                    return self.completed("healthy", 0, nodes_ready=4, nodes_total=4, issues=[])
                llm_attempts += 1
                if llm_attempts == 1:
                    return subprocess.CompletedProcess(command, 1, "llm_status=failed\n", "")
                return subprocess.CompletedProcess(
                    command, 0,
                    "llm_status=healthy\nllm_failed_check_ids=none\nllm_unknown_check_ids=none\n", "",
                )

            with (
                mock.patch.object(ROLLOUT_MODULE, "run", side_effect=fake_run),
                mock.patch.object(ROLLOUT_MODULE.time, "sleep", return_value=None),
                mock.patch.object(ROLLOUT_MODULE, "_PREFLIGHT_ACCEPTANCE", None),
                mock.patch.object(ROLLOUT_MODULE, "_PROGRESS", None),
            ):
                accepted, diagnostics = ROLLOUT_MODULE.run_phase_acceptance(
                    root, runbook, inventory, "full_fleet", ["egpu-a"], ["egpu-a"],
                    "6.18.36-v8-homecluster+",
                )
            self.assertTrue(accepted)
            self.assertEqual(llm_attempts, 2)
            self.assertIn("phase_runtime_acceptance=pass", diagnostics)
            self.assertIn("postboot_acceptance_attempts=2", diagnostics)
            self.assertIn("llm_acceptance=pass", diagnostics)

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

    def test_applied_result_with_nonzero_exit_confirms_mutation_boundary(self):
        reason, stage, committed, next_check = ROLLOUT_MODULE.selector_failure_contract(
            2,
            {"status": "applied"},
        )
        self.assertEqual(reason, "selector_apply_process_failed_after_applied_result")
        self.assertEqual(stage, "selector_postcheck_process_failure")
        self.assertTrue(committed)
        self.assertEqual(next_check, "common_kernel_current_selector_and_kernel")

        diagnostics = ROLLOUT_MODULE.ansible_failure_diagnostics(
            "",
            stage=stage,
            mutation_committed=committed,
            power_cycle_started=False,
            next_check_id=next_check,
        )
        self.assertIn("runtime_mutation_committed=true", diagnostics)
        self.assertIn("power_cycle_started=false", diagnostics)

    def test_planned_result_with_nonzero_exit_does_not_claim_committed_mutation(self):
        reason, stage, committed, next_check = ROLLOUT_MODULE.selector_failure_contract(
            2,
            {"status": "planned"},
        )
        self.assertEqual(reason, "selector_apply_failed")
        self.assertEqual(stage, "selector_mutation_or_postcheck")
        self.assertIsNone(committed)
        self.assertEqual(next_check, "common_kernel_current_selector")

        diagnostics = ROLLOUT_MODULE.ansible_failure_diagnostics(
            "",
            stage=stage,
            mutation_committed=committed,
            power_cycle_started=False,
            next_check_id=next_check,
        )
        self.assertIn("runtime_mutation_committed=unknown", diagnostics)


if __name__ == "__main__":
    unittest.main()
