from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEGACY_TEST = HERE / "pi_rpi5_common_kernel_test_legacy.py"

spec = importlib.util.spec_from_file_location("pi_rpi5_common_kernel_test_legacy", LEGACY_TEST)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)


def test_rollback_uses_only_recorded_previous_selectors(self):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        previous = {"generic-a": {"tftp_release": "20260730-rpi5", "rootfs_release": "20260730-rpi5"}}
        self.write_record(
            root,
            "generic_canary",
            {
                "phase": "generic_canary",
                "targets": ["generic-a"],
                "acceptance_status": "fail",
                "rollback_recommended": True,
                "previous_selector_by_node": previous,
            },
        )
        targets, selectors, failed_phase = legacy.ROLLOUT_MODULE.target_policy(
            root,
            "rollback_last_phase",
            ["generic-a", "generic-b", "egpu-a"],
            "egpu-a",
        )
        self.assertEqual(targets, ["generic-a"])
        self.assertEqual(selectors, previous)
        self.assertEqual(failed_phase, "generic_canary")


def test_rollback_rejects_failed_phase_without_recommendation(self):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        previous = {"generic-a": {"tftp_release": "20260730-rpi5", "rootfs_release": "20260730-rpi5"}}
        self.write_record(
            root,
            "generic_canary",
            {
                "phase": "generic_canary",
                "targets": ["generic-a"],
                "acceptance_status": "fail",
                "rollback_recommended": False,
                "previous_selector_by_node": previous,
            },
        )
        with self.assertRaises(legacy.ROLLOUT_MODULE.RolloutError) as context:
            legacy.ROLLOUT_MODULE.target_policy(
                root,
                "rollback_last_phase",
                ["generic-a", "generic-b", "egpu-a"],
                "egpu-a",
            )
        self.assertEqual(context.exception.reason, "known_rollback_unavailable")


def test_helpers_accept_no_arbitrary_host_release_or_path(self):
    precheck = legacy.PRECHECK.read_text(encoding="utf-8")
    gate = legacy.GATE.read_text(encoding="utf-8")
    rollout = legacy.ROLLOUT.read_text(encoding="utf-8")
    self.assertNotIn('add_argument("--host"', precheck + gate + rollout)
    self.assertNotIn('add_argument("--release"', precheck + gate + rollout)
    self.assertNotIn('add_argument("--path"', precheck + gate + rollout)
    self.assertNotIn('add_argument("--playbook"', precheck + gate + rollout)
    self.assertIn("pi-rpi5-common-kernel-rollout-legacy", rollout)
    self.assertIn("rollback_recommended", rollout)
    self.assertIn("legacy.main()", rollout)


def test_generation_gate_is_builder_scoped_and_rollout_health_is_observation(self):
    precheck = legacy.PRECHECK.read_text(encoding="utf-8")
    rollout = legacy.ROLLOUT.read_text(encoding="utf-8")
    generation_playbook = (
        HERE.parent / "ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml"
    ).read_text(encoding="utf-8")
    precheck_playbook = (
        HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml"
    ).read_text(encoding="utf-8")
    gate_playbook = (
        HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"
    ).read_text(encoding="utf-8")

    self.assertIn("k3s_gate=deferred_to_rollout", precheck)
    self.assertNotIn("k3s_not_healthy", precheck)
    self.assertIn("def observe_cluster_health", rollout)
    self.assertIn("legacy.ensure_cluster_healthy = observe_cluster_health", rollout)
    self.assertIn("pre_rollout_cluster_status", rollout)
    self.assertIn("ignore_unreachable: true", generation_playbook)
    self.assertIn("local-only", generation_playbook)
    self.assertIn("LC_ALL: C", generation_playbook)
    self.assertIn("remote workers are optional for generation precheck", precheck_playbook)
    self.assertIn("homecluster_common_kernel_stg_stage_date_from_openwrt", precheck_playbook)
    self.assertIn("cat /etc/resolv.conf >/dev/null", precheck_playbook)
    self.assertIn("getent ahostsv4 github.com >/dev/null", precheck_playbook)
    self.assertIn("git ls-remote --exit-code", precheck_playbook)
    self.assertNotIn("-print -quit", gate_playbook)
    self.assertIn("sed -n '1p'", gate_playbook)


legacy.PolicyTests.test_rollback_uses_only_recorded_previous_selectors = test_rollback_uses_only_recorded_previous_selectors
legacy.PolicyTests.test_rollback_rejects_failed_phase_without_recommendation = test_rollback_rejects_failed_phase_without_recommendation
legacy.SourceContractTests.test_helpers_accept_no_arbitrary_host_release_or_path = test_helpers_accept_no_arbitrary_host_release_or_path
legacy.SourceContractTests.test_generation_gate_is_builder_scoped_and_rollout_gate_is_cluster_scoped = (
    test_generation_gate_is_builder_scoped_and_rollout_health_is_observation
)

for name in dir(legacy):
    value = getattr(legacy, name)
    if isinstance(value, type) and issubclass(value, unittest.TestCase):
        globals()[name] = value


if __name__ == "__main__":
    unittest.main()
