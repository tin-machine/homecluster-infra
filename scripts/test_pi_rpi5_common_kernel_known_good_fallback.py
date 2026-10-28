from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROLLOUT = HERE / "pi-rpi5-common-kernel-rollout"
PLAYBOOK = HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml"

loader = importlib.machinery.SourceFileLoader("pi_rpi5_common_kernel_rollout_known_good_test", str(ROLLOUT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SelectorConvergenceTests(unittest.TestCase):
    def setUp(self) -> None:
        module._CURRENT_ACTION = "full_fleet"
        module._LAST_FRESH_BOOT_STATUS = ""
        module._LAST_KERNEL_ACCEPTANCE = None
        module._LAST_SELECTOR_CHANGED = None
        module._LAST_ROLLOUT_CONTEXT = {}
        module._PREFLIGHT_ACCEPTANCE = None

    def test_equal_selectors_are_already_aligned(self) -> None:
        selector = {"tftp_release": "20260828-rpi5", "rootfs_release": "20260828-rpi5"}
        value = {
            "previous_selector_by_node": {"node-a": selector, "node-b": selector},
            "applied_selector_by_node": {"node-a": dict(selector), "node-b": dict(selector)},
        }
        self.assertFalse(module.selectors_changed(["node-a", "node-b"], value))

    def test_one_different_selector_requires_change(self) -> None:
        value = {
            "previous_selector_by_node": {
                "node-a": {"tftp_release": "20260827-rpi5", "rootfs_release": "20260827-rpi5"},
                "node-b": {"tftp_release": "20260828-rpi5", "rootfs_release": "20260828-rpi5"},
            },
            "applied_selector_by_node": {
                "node-a": {"tftp_release": "20260828-rpi5", "rootfs_release": "20260828-rpi5"},
                "node-b": {"tftp_release": "20260828-rpi5", "rootfs_release": "20260828-rpi5"},
            },
        }
        self.assertTrue(module.selectors_changed(["node-a", "node-b"], value))



    def test_same_selector_requires_this_generations_fresh_boot(self) -> None:
        module._LAST_SELECTOR_CHANGED = False
        module._PROGRESS = None
        with patch.object(module, "run_phase_acceptance", side_effect=AssertionError("no reused acceptance")), \
             patch.object(module, "_original_run_fresh_boot", return_value=("fresh-run", "pass")) as power:
            self.assertEqual(module.run_fresh_boot(Path("/fixture/runbook")), ("fresh-run", "pass"))
        power.assert_called_once()
        self.assertEqual(module._LAST_FRESH_BOOT_STATUS, "pass")

    def test_no_reboot_acceptance_is_recorded_explicitly(self) -> None:
        selector = {"tftp_release": "20260828-rpi5", "rootfs_release": "20260828-rpi5"}
        module._LAST_SELECTOR_CHANGED = False
        module._LAST_KERNEL_ACCEPTANCE = True
        record = module.phase_record_value(
            phase="full_fleet",
            targets=["node-a"],
            selector_result={
                "previous_selector_by_node": {"node-a": selector},
                "applied_selector_by_node": {"node-a": dict(selector)},
            },
            accepted={
                "generation_run_id": "20260828T000000Z-stg-generation-old",
                "exact_kernel_release": "6.18.36-v8-homecluster+",
            },
            fresh_boot_run_id="",
            acceptance_status="pass",
            started_at="2026-09-13T00:00:00Z",
        )
        self.assertEqual(record["acceptance_status"], "pass")
        self.assertFalse(record["selector_changed"])
        self.assertEqual(record["reboot_acceptance_status"], "not_required")
        self.assertEqual(record["kernel_acceptance_status"], "pass")
        self.assertFalse(record["rollback_recommended"])


class PlaybookContractTests(unittest.TestCase):
    def test_full_fleet_is_allowed_and_selector_switch_is_conditional(self) -> None:
        source = PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("'full_fleet'", source)
        self.assertIn("homecluster_common_kernel_selector_change_required", source)
        self.assertIn("when: homecluster_common_kernel_selector_change_required | bool", source)
        self.assertIn("selector_already_aligned", source)


if __name__ == "__main__":
    unittest.main()
