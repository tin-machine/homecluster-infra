from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSPECT = HERE / "pi-rpi5-common-kernel-inspect"
SELECTOR_PLAYBOOK = HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-selector-inspect.yml"

loader = importlib.machinery.SourceFileLoader("pi_rpi5_common_kernel_inspect_test", str(INSPECT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ACCEPTED_SELECTOR = "20260828-rpi5"
EXACT_RELEASE = "6.18.36-v8-homecluster+"


def classify(
    *,
    tftp: str = ACCEPTED_SELECTOR,
    rootfs: str = ACCEPTED_SELECTOR,
    running: str = EXACT_RELEASE,
    acceptance: str = "pass",
    reason: str = "runtime_acceptance_pass",
):
    return module.classify_node(
        current_tftp_selector=tftp,
        current_rootfs_selector=rootfs,
        accepted_selector=ACCEPTED_SELECTOR,
        running_kernel_release=running,
        exact_kernel_release=EXACT_RELEASE,
        runtime_acceptance=acceptance,
        runtime_reason=reason,
    )


class RequiredActionTests(unittest.TestCase):
    def test_aligned_runtime_pass_requires_none(self) -> None:
        value = classify()
        self.assertTrue(value["selector_aligned"])
        self.assertTrue(value["runtime_release_matches"])
        self.assertEqual(value["runtime_acceptance"], "pass")
        self.assertEqual(value["required_action"], "none")

    def test_aligned_runtime_fail_requires_fresh_boot(self) -> None:
        value = classify(acceptance="fail", reason="kernel_hard_gate_failed")
        self.assertTrue(value["selector_aligned"])
        self.assertEqual(value["runtime_acceptance"], "fail")
        self.assertEqual(value["required_action"], "fresh_boot")

    def test_selector_mismatch_requires_switch_and_fresh_boot(self) -> None:
        value = classify(tftp="20260827-rpi5", rootfs="20260827-rpi5")
        self.assertFalse(value["selector_aligned"])
        self.assertEqual(value["required_action"], "selector_switch_and_fresh_boot")

    def test_generic_nvidia_unexpected_load_is_runtime_failure(self) -> None:
        value = classify(acceptance="fail", reason="generic_nvidia_autoloaded")
        self.assertEqual(value["runtime_reason"], "generic_nvidia_autoloaded")
        self.assertEqual(value["required_action"], "fresh_boot")

    def test_egpu_nvidia_or_llm_failure_is_runtime_failure(self) -> None:
        value = classify(acceptance="fail", reason="egpu_nvidia_or_llm_service_failed")
        self.assertEqual(value["runtime_reason"], "egpu_nvidia_or_llm_service_failed")
        self.assertEqual(value["required_action"], "fresh_boot")

    def test_egpu_llm_helper_failure_is_runtime_failure(self) -> None:
        value = classify(acceptance="fail", reason="egpu_llm_acceptance_failed")
        self.assertEqual(value["runtime_reason"], "egpu_llm_acceptance_failed")
        self.assertEqual(value["required_action"], "fresh_boot")

    def test_node_unreachable_is_blocked_not_none(self) -> None:
        value = classify(running="", acceptance="unknown", reason="node_unreachable")
        self.assertEqual(value["runtime_acceptance"], "unknown")
        self.assertEqual(value["runtime_reason"], "node_unreachable")
        self.assertEqual(value["required_action"], "blocked")

    def test_runtime_pass_with_wrong_kernel_is_normalized_to_failure(self) -> None:
        value = classify(running="6.18.35-v8-homecluster+")
        self.assertFalse(value["runtime_release_matches"])
        self.assertEqual(value["runtime_acceptance"], "fail")
        self.assertEqual(value["runtime_reason"], "kernel_release_mismatch")
        self.assertEqual(value["required_action"], "fresh_boot")


class FailureReasonTests(unittest.TestCase):
    def test_existing_phase_acceptance_task_names_map_to_fixed_reasons(self) -> None:
        cases = {
            "ansible_failed_task=Pi5 common kernel runtime hard gateを検証": "kernel_hard_gate_failed",
            "ansible_failed_task=Generic canaryでNVIDIA module非autoloadを検証": "generic_nvidia_autoloaded",
            "ansible_failed_task=eGPU canaryでNVIDIAとLLM serviceを検証": "egpu_nvidia_or_llm_service_failed",
            "llm_acceptance=fail": "egpu_llm_acceptance_failed",
        }
        for diagnostic, expected in cases.items():
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(module.failure_reason([diagnostic], EXACT_RELEASE, EXACT_RELEASE), expected)


class FixtureContractTests(unittest.TestCase):
    def write_fixture(self, value: dict[str, object]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "fixture.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def base_fixture(self) -> dict[str, object]:
        return {
            "accepted_generation_run_id": "20260910T154244Z-stg-generation-847497",
            "accepted_selector": ACCEPTED_SELECTOR,
            "exact_kernel_release": EXACT_RELEASE,
            "nodes": {
                "node-a": {
                    "current_tftp_selector": ACCEPTED_SELECTOR,
                    "current_rootfs_selector": ACCEPTED_SELECTOR,
                    "running_kernel_release": EXACT_RELEASE,
                    "runtime_acceptance": "pass",
                    "runtime_reason": "runtime_acceptance_pass",
                }
            },
        }

    def test_json_schema_and_required_action_vocabulary(self) -> None:
        result = module.fixture_result(self.write_fixture(self.base_fixture()))
        self.assertEqual(result["schema"], "rpi5-common-kernel-runtime-inspection-v1")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["reason"], "runtime_inspection_complete")
        self.assertEqual(set(module.REQUIRED_ACTIONS), {"none", "fresh_boot", "selector_switch_and_fresh_boot", "blocked"})
        node = result["nodes"]["node-a"]
        self.assertEqual(
            set(node),
            {
                "current_tftp_selector",
                "current_rootfs_selector",
                "selector_aligned",
                "running_kernel_release",
                "runtime_release_matches",
                "runtime_acceptance",
                "runtime_reason",
                "required_action",
            },
        )
        self.assertIn(node["required_action"], module.REQUIRED_ACTIONS)

    def test_unknown_node_makes_inspection_incomplete(self) -> None:
        fixture = self.base_fixture()
        fixture["nodes"] = {
            "node-a": {
                "current_tftp_selector": ACCEPTED_SELECTOR,
                "current_rootfs_selector": ACCEPTED_SELECTOR,
                "running_kernel_release": "",
                "runtime_acceptance": "unknown",
                "runtime_reason": "node_unreachable",
            }
        }
        result = module.fixture_result(self.write_fixture(fixture))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "runtime_inspection_incomplete")
        self.assertEqual(result["nodes"]["node-a"]["required_action"], "blocked")


class ReadOnlyBoundaryTests(unittest.TestCase):
    def test_inspection_source_has_no_runtime_mutation_entrypoint(self) -> None:
        source = INSPECT.read_text(encoding="utf-8")
        for forbidden in (
            "HOMECLUSTER_RPI5_COMMON_KERNEL_ROLLOUT_APPLY",
            "HOMECLUSTER_K3S_FRESH_BOOT_POWER_APPLY",
            "run_fresh_boot(",
            "tftp_switch",
            "SwitchBot",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertNotIn("--host", source)

    def test_inspection_reuses_phase_acceptance_without_copying_hard_gates(self) -> None:
        source = INSPECT.read_text(encoding="utf-8")
        self.assertIn("run_phase_acceptance(", source)
        self.assertNotIn("getconf PAGESIZE", source)
        self.assertNotIn("lsmod", source)
        self.assertNotIn("systemctl is-active", source)

    def test_selector_playbook_is_read_only(self) -> None:
        source = SELECTOR_PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("tasks_from: pxe_host_releases", source)
        self.assertIn("selector_inspection_complete", source)
        self.assertNotIn("tasks_from: tftp_switch", source)
        self.assertNotIn("openwrt_pxe_hosts_effective_override", source)
        self.assertNotIn("homecluster_common_kernel_selector_change_required", source)


if __name__ == "__main__":
    unittest.main()
