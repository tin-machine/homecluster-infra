from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEGACY_TEST = HERE / "pi_rpi5_common_kernel_test_legacy.py"
SOURCE_CHECK = HERE / "ci/check-rpi5-common-kernel-build.py"

spec = importlib.util.spec_from_file_location("pi_rpi5_common_kernel_test_legacy", LEGACY_TEST)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)

source_check_spec = importlib.util.spec_from_file_location("check_rpi5_common_kernel_build", SOURCE_CHECK)
assert source_check_spec and source_check_spec.loader
source_check = importlib.util.module_from_spec(source_check_spec)
source_check_spec.loader.exec_module(source_check)

PHASE_ACCEPTANCE_PLAYBOOK = (
    HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-phase-acceptance.yml"
)
SAFE_ROUTE_CHECK = 'test -n "$(ip route show)"'
UNSAFE_ROUTE_CHECK = "ip route show | grep -q ."


class PhaseAcceptanceRouteTests(unittest.TestCase):
    def route_check(self) -> str:
        source = PHASE_ACCEPTANCE_PLAYBOOK.read_text(encoding="utf-8")
        matches = [line.strip() for line in source.splitlines() if "ip route show" in line]
        self.assertEqual(matches, [SAFE_ROUTE_CHECK])
        self.assertNotIn(UNSAFE_ROUTE_CHECK, source)
        return matches[0]

    def run_route_check(self, output_lines: int) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ip = directory / "ip"
            ip.write_text(
                "#!/bin/sh\n"
                "test \"$*\" = \"route show\" || exit 2\n"
                f"count={output_lines}\n"
                "index=0\n"
                "while [ \"$index\" -lt \"$count\" ]; do\n"
                "  printf 'default via 192.0.2.1 dev eth0\\n'\n"
                "  index=$((index + 1))\n"
                "done\n",
                encoding="utf-8",
            )
            ip.chmod(0o755)
            environment = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"}
            return subprocess.run(
                ["bash", "-o", "pipefail", "-c", self.route_check()],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

    def test_many_routes_pass_without_sigpipe(self):
        completed = self.run_route_check(4096)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_route_fails(self):
        completed = self.run_route_check(0)
        self.assertNotEqual(completed.returncode, 0)


class GateFailureDiagnosticsTests(unittest.TestCase):
    def diagnostics(self, output: str) -> list[str]:
        completed = subprocess.CompletedProcess(["ansible-playbook"], 2, output, "")
        return legacy.GATE_MODULE.playbook_failure_diagnostics(completed)

    def test_uses_last_allow_listed_check_id_from_fatal_line(self):
        diagnostics = self.diagnostics(
            "TASK [Pi5 common kernel OpenWrt PXE artifactsを検証]\n"
            'fatal: [openwrt-fixture]: FAILED! => {"rc": 1, "stdout": '
            '"HOMECLUSTER_CHECK_ID=module_tree_present\\n'
            'HOMECLUSTER_CHECK_ID=generic_initramfs_identical"}\n'
        )
        self.assertIn("failed_check_id=generic_initramfs_identical", diagnostics)
        self.assertNotIn("failed_check_id=module_tree_present", diagnostics)

    def test_ignores_unknown_check_id(self):
        diagnostics = self.diagnostics(
            'fatal: [openwrt-fixture]: FAILED! => {"rc": 1, '
            '"stdout": "HOMECLUSTER_CHECK_ID=arbitrary_value"}\n'
        )
        self.assertFalse(any(item.startswith("failed_check_id=") for item in diagnostics))

    def test_ignores_check_id_outside_fatal_line(self):
        diagnostics = self.diagnostics(
            "HOMECLUSTER_CHECK_ID=nvidia_nfs_present\n"
            'fatal: [openwrt-fixture]: FAILED! => {"rc": 1}\n'
        )
        self.assertFalse(any(item.startswith("failed_check_id=") for item in diagnostics))

    def test_playbook_check_ids_match_parser_allow_list(self):
        playbook = (
            HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"
        ).read_text(encoding="utf-8")
        direct_ids = set(
            re.findall(r"^\s*homecluster_check ([a-z0-9_]+)$", playbook, re.MULTILINE)
        )
        image_suffixes = set(
            re.findall(
                r'^\s*homecluster_check "\$\{image_check_prefix\}_([a-z0-9_]+)"$',
                playbook,
                re.MULTILINE,
            )
        )
        playbook_ids = direct_ids | {
            f"{prefix}_{suffix}"
            for prefix in ("generic", "nvidia")
            for suffix in image_suffixes
        }
        self.assertEqual(playbook_ids, legacy.GATE_MODULE.ARTIFACT_CHECK_IDS)


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


def test_build_and_pxe_release_dates_are_independent(self):
    builder = {
        "rpi5_common_kernel_build_enabled": True,
        "rpi5_common_kernel_build_apply": True,
        "rpi5_egpu_nvidia_artifact_bundle_enabled": True,
        "rpi5_egpu_nvidia_artifact_bundle_apply": True,
        "openwrt_rpi5_egpu_generation_enabled": True,
        "openwrt_rpi5_egpu_generation_apply": True,
        "openwrt_rpi5_egpu_generation_artifact_bundle_enabled": True,
        "rpi5_common_kernel_build_confirm": "build",
        "rpi5_common_kernel_build_confirm_expected": "build",
        "rpi5_egpu_nvidia_artifact_bundle_confirm": "bundle",
        "rpi5_egpu_nvidia_artifact_bundle_confirm_expected": "bundle",
        "rpi5_common_kernel_build_stage": "stg",
        "rpi5_common_kernel_build_stage_date": "20260820",
        "rpi5_common_kernel_build_source_version": "a" * 40,
        "rpi5_common_kernel_build_open_modules_commit": "b" * 40,
        "rpi5_common_kernel_build_config_seed": "/var/lib/rancher/k3s/config/kernel",
        "rpi5_common_kernel_build_open_modules_dir": "/var/lib/rancher/k3s/open-modules",
        "rpi5_common_kernel_build_nvidia_runfile_path": "/var/lib/rancher/k3s/nvidia.run",
    }
    legacy.PRECHECK_MODULE.validate_private_contract(builder)
    legacy.PRECHECK_MODULE.validate_pxe_release_contract(
        {"openwrt_gentoo_release_bundle_stage_dates": {"stg": "20260826"}}
    )
    self.assertNotEqual(builder["rpi5_common_kernel_build_stage_date"], "20260826")


def test_invalid_pxe_release_identity_is_rejected(self):
    with self.assertRaises(legacy.PRECHECK_MODULE.PrecheckError) as context:
        legacy.PRECHECK_MODULE.validate_pxe_release_contract(
            {"openwrt_gentoo_release_bundle_stage_dates": {"stg": "invalid"}}
        )
    self.assertEqual(context.exception.reason, "pxe_release_identity_invalid")


def test_generation_entrypoint_release_date_wiring_rejects_kernel_artifact_date(self):
    positive = """
- ansible.builtin.import_playbook: pxe-release-bundle-build.yml
  vars:
    pxe_release_bundle_stage: stg
    pxe_release_bundle_date: "{{ openwrt_gentoo_release_bundle_stage_dates[pxe_release_bundle_stage] }}"
"""
    source_check.validate_generation_entrypoint_release_date(positive)

    negative = positive.replace(
        "openwrt_gentoo_release_bundle_stage_dates[pxe_release_bundle_stage]",
        "rpi5_common_kernel_build_stage_date",
    )
    with self.assertRaises(AssertionError):
        source_check.validate_generation_entrypoint_release_date(negative)


def test_common_kernel_publish_contract_rejects_missing_hash_gate(self):
    positive = """
- ansible.builtin.copy:
    unsafe_writes: false
  loop:
    - src: kernel8-homecluster.img
      dest: kernel8.img
- ansible.builtin.stat:
  register: pxe_rpi5_common_kernel_publish_stats
- ansible.builtin.assert:
    that:
      - pxe_rpi5_common_kernel_publish_stats.results[0].stat.checksum == pxe_rpi5_common_kernel_publish_stats.results[1].stat.checksum
"""
    source_check.validate_common_kernel_publish_contract(positive)

    negative = positive.replace(
        "pxe_rpi5_common_kernel_publish_stats.results[0].stat.checksum == pxe_rpi5_common_kernel_publish_stats.results[1].stat.checksum",
        "true",
    )
    with self.assertRaises(AssertionError):
        source_check.validate_common_kernel_publish_contract(negative)


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


def test_generation_gate_uses_artifact_reference_not_stage_date_equality(self):
    precheck = legacy.PRECHECK.read_text(encoding="utf-8")
    rollout = legacy.ROLLOUT.read_text(encoding="utf-8")
    generation_playbook = (HERE.parent / "ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml").read_text(encoding="utf-8")
    generation_entrypoint = (
        HERE.parent / "ansible/openwrt/playbooks/pxe-release-bundle-staging-with-common-kernel.yml"
    ).read_text(encoding="utf-8")
    artifact_role = (HERE.parent / "ansible/arm64/roles/rpi5_egpu_nvidia_artifact_bundle/tasks/main.yml").read_text(encoding="utf-8")
    precheck_playbook = (HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml").read_text(encoding="utf-8")
    gate_playbook = (HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml").read_text(encoding="utf-8")

    self.assertIn("k3s_gate=deferred_to_rollout", precheck)
    self.assertNotIn("common_kernel_stage_date_mismatch", precheck)
    self.assertNotIn("validate_stage_date_alignment", precheck)
    self.assertIn("kernel_artifact_and_pxe_release_identity=independent", precheck)
    self.assertIn("def observe_cluster_health", rollout)
    self.assertIn("pre_rollout_cluster_status", rollout)

    self.assertNotIn("rpi5_common_kernel_stage_date_from_openwrt", generation_playbook)
    self.assertNotIn("rpi5_common_kernel_build_stage_date:", generation_playbook)
    self.assertIn("PXE release date is resolved independently", generation_playbook)
    self.assertIn("pxe_release_bundle_stage: stg", generation_entrypoint)
    self.assertIn("openwrt_gentoo_release_bundle_stage_dates", generation_entrypoint)
    self.assertIn("pxe_release_bundle_date:", generation_entrypoint)
    self.assertNotIn("rpi5_common_kernel_build_stage_date", generation_entrypoint)
    self.assertIn("hosts: openwrt", generation_entrypoint)
    self.assertIn("homecluster_common_kernel_builder_host", generation_entrypoint)
    self.assertIn("openwrt_rpi5_egpu_generation_manifest_metadata", generation_entrypoint)
    self.assertIn("common_kernel_artifact", generation_entrypoint)
    self.assertNotIn("pxe-release-bundle-staging.yml", generation_entrypoint)
    self.assertIn("common_kernel_artifact", artifact_role)
    self.assertIn("sha256:", artifact_role)

    self.assertNotIn("homecluster_common_kernel_stg_stage_date_from_openwrt", precheck_playbook)
    self.assertIn("PXE release identity", precheck_playbook)
    self.assertIn("remote workers are optional for generation precheck", precheck_playbook)
    self.assertIn("cat /etc/resolv.conf >/dev/null", precheck_playbook)
    self.assertIn("homecluster_common_kernel_build_source_repo_default: https://github.com/raspberrypi/linux.git", precheck_playbook)
    self.assertIn("rpi5_common_kernel_build_source_repo", precheck_playbook)
    self.assertIn("| default(homecluster_common_kernel_build_source_repo_default, true)", precheck_playbook)
    self.assertIn("ansible.builtin.command:", precheck_playbook)
    self.assertIn("- ls-remote", precheck_playbook)

    self.assertIn("common_kernel_artifact", gate_playbook)
    self.assertIn("pxe_release_manifest_sha256", gate_playbook)
    self.assertIn("rpi5-common-kernel-gate-v2", gate_playbook)
    self.assertNotIn("homecluster_common_kernel_gate_candidate_selector", gate_playbook)


legacy.PolicyTests.test_rollback_uses_only_recorded_previous_selectors = test_rollback_uses_only_recorded_previous_selectors
legacy.PolicyTests.test_rollback_rejects_failed_phase_without_recommendation = test_rollback_rejects_failed_phase_without_recommendation
legacy.PrecheckPolicyTests.test_stage_date_alignment_accepts_match = test_build_and_pxe_release_dates_are_independent
legacy.PrecheckPolicyTests.test_stage_date_alignment_rejects_mismatch = test_invalid_pxe_release_identity_is_rejected
legacy.PrecheckPolicyTests.test_generation_entrypoint_release_date_wiring_rejects_kernel_artifact_date = (
    test_generation_entrypoint_release_date_wiring_rejects_kernel_artifact_date
)
legacy.SourceContractTests.test_common_kernel_publish_contract_rejects_missing_hash_gate = (
    test_common_kernel_publish_contract_rejects_missing_hash_gate
)
legacy.SourceContractTests.test_helpers_accept_no_arbitrary_host_release_or_path = test_helpers_accept_no_arbitrary_host_release_or_path
legacy.SourceContractTests.test_generation_gate_is_builder_scoped_and_rollout_gate_is_cluster_scoped = (
    test_generation_gate_uses_artifact_reference_not_stage_date_equality
)

for name in dir(legacy):
    value = getattr(legacy, name)
    if isinstance(value, type) and issubclass(value, unittest.TestCase):
        globals()[name] = value


if __name__ == "__main__":
    unittest.main()
