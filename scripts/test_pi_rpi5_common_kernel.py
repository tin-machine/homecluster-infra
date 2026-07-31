from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRECHECK = HERE / "pi-rpi5-common-kernel-precheck"
GATE = HERE / "pi-rpi5-common-kernel-gate"
ROLLOUT = HERE / "pi-rpi5-common-kernel-rollout"


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ROLLOUT_MODULE = load(ROLLOUT, "pi_rpi5_common_kernel_rollout")


class CliFixtureTests(unittest.TestCase):
    def fixture(self, value: dict) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "fixture.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return temporary, path

    def run_json(self, script: Path, *args: str):
        completed = subprocess.run(
            [sys.executable, str(script), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        value = json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else None
        return completed, value

    def test_source_only_precheck_fixture(self):
        temporary, fixture = self.fixture({"source_status": "pass", "diagnostics": ["fixed_playbook_contract=pass"]})
        try:
            completed, value = self.run_json(PRECHECK, "--source-only", "--json", "--fixture", str(fixture))
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(value["status"], "pass")
        self.assertTrue(value["source_only"])

    def test_precheck_blocked_fixture(self):
        temporary, fixture = self.fixture({"status": "blocked", "reason": "distccd_inactive"})
        try:
            completed, value = self.run_json(PRECHECK, "--json", "--fixture", str(fixture))
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(value["reason"], "distccd_inactive")

    def test_generation_gate_fixture(self):
        temporary, fixture = self.fixture({"status": "pass"})
        try:
            completed, value = self.run_json(
                GATE,
                "--json",
                "--observer-run-id",
                "20260731T000000Z-stg-generation-1",
                "--fixture",
                str(fixture),
            )
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(value["status"], "pass")
        self.assertRegex(value["exact_kernel_release"], r"-v8-homecluster\+$")

    def test_generation_gate_rejects_unsafe_run_id(self):
        temporary, fixture = self.fixture({"status": "pass"})
        try:
            completed, value = self.run_json(
                GATE,
                "--json",
                "--observer-run-id",
                "../../unsafe",
                "--fixture",
                str(fixture),
            )
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(value["reason"], "generation_run_id_invalid")

    def test_rollout_requires_apply_gate(self):
        completed, value = self.run_json(ROLLOUT, "--json", "--phase", "generic_canary")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(value["reason"], "missing_HOMECLUSTER_RPI5_COMMON_KERNEL_ROLLOUT_APPLY")

    def test_rollout_fixture_returns_phase_record(self):
        temporary, fixture = self.fixture({"status": "pass", "targets": ["rpi5-01"]})
        try:
            completed, value = self.run_json(
                ROLLOUT,
                "--json",
                "--phase",
                "generic_canary",
                "--fixture",
                str(fixture),
            )
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(value["phase_record"]["phase"], "generic_canary")
        self.assertEqual(value["phase_record"]["acceptance_status"], "pass")

    def test_rollback_fixture_is_separate_record(self):
        temporary, fixture = self.fixture({"status": "pass", "record_phase": "generic_canary"})
        try:
            completed, value = self.run_json(
                ROLLOUT,
                "--json",
                "--phase",
                "rollback_last_phase",
                "--fixture",
                str(fixture),
            )
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(value["phase_record"]["phase"], "generic_canary")
        self.assertEqual(value["phase_record"]["acceptance_status"], "rolled_back")

    def test_unknown_phase_is_rejected_by_parser(self):
        completed = subprocess.run(
            [sys.executable, str(ROLLOUT), "--json", "--phase", "arbitrary-host"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid choice", completed.stderr)


class PolicyTests(unittest.TestCase):
    def write_record(self, root: Path, phase: str, value: dict) -> None:
        directory = root / "rollout-phases"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{phase}.json").write_text(json.dumps(value), encoding="utf-8")

    def test_rollout_order_and_fixed_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "generic_canary", ["rpi5-01", "rpi5-02", "rpi5-03"])
            self.assertEqual((targets, phase), (["rpi5-01"], "generic_canary"))
            self.write_record(root, "generic_canary", {"acceptance_status": "pass", "targets": ["rpi5-01"]})
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "egpu_canary", ["rpi5-01", "rpi5-02", "rpi5-03"])
            self.assertEqual((targets, phase), (["rpi5-03"], "egpu_canary"))
            self.write_record(root, "egpu_canary", {"acceptance_status": "pass", "targets": ["rpi5-03"]})
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "fleet_rollout", ["rpi5-01", "rpi5-02", "rpi5-03"])
            self.assertEqual((targets, phase), (["rpi5-02"], "fleet_rollout"))

    def test_rollback_uses_only_recorded_previous_selectors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = {"rpi5-01": {"tftp_release": "20260730-rpi5", "rootfs_release": "20260730-rpi5"}}
            self.write_record(
                root,
                "generic_canary",
                {
                    "phase": "generic_canary",
                    "targets": ["rpi5-01"],
                    "acceptance_status": "fail",
                    "previous_selector_by_node": previous,
                },
            )
            targets, selectors, failed_phase = ROLLOUT_MODULE.target_policy(
                root,
                "rollback_last_phase",
                ["rpi5-01", "rpi5-02", "rpi5-03"],
            )
            self.assertEqual(targets, ["rpi5-01"])
            self.assertEqual(selectors, previous)
            self.assertEqual(failed_phase, "generic_canary")


class SourceContractTests(unittest.TestCase):
    def test_helpers_accept_no_arbitrary_host_release_or_path(self):
        precheck = PRECHECK.read_text(encoding="utf-8")
        gate = GATE.read_text(encoding="utf-8")
        rollout = ROLLOUT.read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--host"', precheck + gate + rollout)
        self.assertNotIn('add_argument("--release"', precheck + gate + rollout)
        self.assertNotIn('add_argument("--path"', precheck + gate + rollout)
        self.assertNotIn('add_argument("--playbook"', precheck + gate + rollout)
        self.assertIn("automatic_rollback=false", rollout)
        self.assertIn("choices=PHASES", rollout)

    def test_rollout_playbook_records_previous_selector_before_mutation(self):
        playbook = (HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml").read_text(encoding="utf-8")
        planned = playbook.index("pre-mutation plan")
        applied = playbook.index("fixed PXE selectorを適用")
        self.assertLess(planned, applied)
        self.assertIn("previous_selector_by_node", playbook)
        self.assertIn("openwrt_pxe_hosts_effective_override", playbook)


if __name__ == "__main__":
    unittest.main()
