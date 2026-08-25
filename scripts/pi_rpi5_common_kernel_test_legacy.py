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


PRECHECK_MODULE = load(PRECHECK, "pi_rpi5_common_kernel_precheck")
GATE_MODULE = load(GATE, "pi_rpi5_common_kernel_gate")
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

    def test_generation_precheck_fixture_does_not_require_cluster_health(self):
        temporary, fixture = self.fixture({"status": "pass"})
        try:
            completed, value = self.run_json(PRECHECK, "--json", "--fixture", str(fixture))
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(value["status"], "pass")
        self.assertEqual(value["cluster_status"], "not_required_for_generation")

    def test_precheck_blocked_fixture(self):
        temporary, fixture = self.fixture({"status": "blocked", "reason": "builder_unavailable"})
        try:
            completed, value = self.run_json(PRECHECK, "--json", "--fixture", str(fixture))
        finally:
            temporary.cleanup()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(value["reason"], "builder_unavailable")

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
        self.assertEqual(value["validation_infra_commit"], "a" * 40)
        self.assertEqual(value["revision_compatibility"], "exact")

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
        temporary, fixture = self.fixture({"status": "pass", "targets": ["generic-a"]})
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


class PrecheckPolicyTests(unittest.TestCase):
    def test_stage_date_alignment_accepts_match(self):
        PRECHECK_MODULE.validate_stage_date_alignment(
            {"rpi5_common_kernel_build_stage_date": "20260822"},
            {"openwrt_gentoo_release_bundle_stage_dates": {"stg": "20260822"}},
        )

    def test_stage_date_alignment_rejects_mismatch(self):
        with self.assertRaises(PRECHECK_MODULE.PrecheckError) as context:
            PRECHECK_MODULE.validate_stage_date_alignment(
                {"rpi5_common_kernel_build_stage_date": "20260821"},
                {"openwrt_gentoo_release_bundle_stage_dates": {"stg": "20260822"}},
            )
        self.assertEqual(context.exception.reason, "common_kernel_stage_date_mismatch")


class GateRevisionPolicyTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[str, str]:
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "fixture"], check=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", root, "checkout", "-qb", "stg"], check=True)
        gate = root / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"
        gate.parent.mkdir(parents=True)
        gate.write_text("gate-v1\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "."], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "generation source"], check=True)
        generation = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
        gate.write_text("gate-v2\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "."], check=True)
        subprocess.run(["git", "-C", root, "commit", "-qm", "gate only"], check=True)
        validation = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
        return generation, validation

    def test_gate_only_descendant_revision_is_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation, validation = self.make_repo(root)
            changed = GATE_MODULE.validate_gate_revision(root, generation, validation)
            self.assertEqual(changed, ["ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"])

    def test_generation_affecting_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation, _ = self.make_repo(root)
            role = root / "ansible/arm64/roles/rpi5_common_kernel_build/tasks/main.yml"
            role.parent.mkdir(parents=True)
            role.write_text("generation-change\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "generation change"], check=True)
            current = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
            with self.assertRaises(GATE_MODULE.GateError) as context:
                GATE_MODULE.validate_gate_revision(root, generation, current)
            self.assertEqual(context.exception.reason, "generation_source_changed_since_observer")


class PolicyTests(unittest.TestCase):
    def write_record(self, root: Path, phase: str, value: dict) -> None:
        directory = root / "rollout-phases"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{phase}.json").write_text(json.dumps(value), encoding="utf-8")

    def test_rollout_order_and_group_driven_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agents = ["generic-a", "generic-b", "egpu-a"]
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "generic_canary", agents, "egpu-a")
            self.assertEqual((targets, phase), (["generic-a"], "generic_canary"))
            self.write_record(root, "generic_canary", {"acceptance_status": "pass", "targets": ["generic-a"]})
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "egpu_canary", agents, "egpu-a")
            self.assertEqual((targets, phase), (["egpu-a"], "egpu_canary"))
            self.write_record(root, "egpu_canary", {"acceptance_status": "pass", "targets": ["egpu-a"]})
            targets, _, phase = ROLLOUT_MODULE.target_policy(root, "fleet_rollout", agents, "egpu-a")
            self.assertEqual((targets, phase), (["generic-b"], "fleet_rollout"))

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
                    "previous_selector_by_node": previous,
                },
            )
            targets, selectors, failed_phase = ROLLOUT_MODULE.target_policy(
                root,
                "rollback_last_phase",
                ["generic-a", "generic-b", "egpu-a"],
                "egpu-a",
            )
            self.assertEqual(targets, ["generic-a"])
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
        self.assertIn('AGENT_GROUP = "k3s_stg_agents"', rollout)
        self.assertIn('EGPU_GROUP = "rpi5_egpu_artifact_bundle"', rollout)

    def test_generation_gate_is_builder_scoped_and_rollout_gate_is_cluster_scoped(self):
        precheck = PRECHECK.read_text(encoding="utf-8")
        rollout = ROLLOUT.read_text(encoding="utf-8")
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
        self.assertIn("def ensure_cluster_healthy", rollout)
        self.assertIn("if args.phase in RUNTIME_PHASES", rollout)
        self.assertLess(rollout.index("ensure_cluster_healthy(runbook)"), rollout.index("selector_apply = run"))
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

    def test_rollout_playbook_records_previous_selector_before_mutation(self):
        playbook = (HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml").read_text(encoding="utf-8")
        planned = playbook.index("pre-mutation plan")
        applied = playbook.index("fixed PXE selectorを適用")
        self.assertLess(planned, applied)
        self.assertIn("previous_selector_by_node", playbook)
        self.assertIn("openwrt_pxe_hosts_effective_override", playbook)


if __name__ == "__main__":
    unittest.main()