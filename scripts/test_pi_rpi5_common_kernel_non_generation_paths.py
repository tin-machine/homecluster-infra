from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "pi-rpi5-common-kernel-gate"


def load_gate():
    loader = importlib.machinery.SourceFileLoader("common_kernel_gate_non_generation_fixture", str(GATE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_gate()

EXPECTED_NON_GENERATION_PATHS = {
    ".agents/skills/homecluster-convergence-monitor/scripts/collect-k3s-convergence.sh",
    ".agents/skills/homecluster-convergence-monitor/scripts/test-collect-k3s-convergence.sh",
    ".github/workflows/static-check.yml",
    "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml",
    "ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml",
    "ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml",
    "docs/troubleshooting/k3s-status-ssh-stderr-json-contamination.md",
    "scripts/ci/check-k3s-status-collector-contract.sh",
    "scripts/ci/check-pxe-initramfs-contract.py",
    "scripts/pi-rpi5-common-kernel-gate",
    "scripts/pi-rpi5-common-kernel-rollout",
    "scripts/test_pi_rpi5_common_kernel.py",
    "scripts/test_pi_rpi5_common_kernel_non_generation_paths.py",
    "scripts/test_pi_rpi5_common_kernel_rollout_health.py",
    "scripts/test_pi_rpi5_common_kernel_rollout_selector_resolution.py",
}


class NonGenerationPathGateTests(unittest.TestCase):
    def test_explicit_rollout_and_observability_paths_are_allowed(self) -> None:
        self.assertTrue(EXPECTED_NON_GENERATION_PATHS <= MODULE.NON_GENERATION_PATHS)

    def test_validation_revision_accepts_only_explicit_control_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", root], check=True)
            subprocess.run(["git", "-C", root, "config", "user.name", "fixture"], check=True)
            subprocess.run(["git", "-C", root, "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", root, "checkout", "-qb", "stg"], check=True)

            marker = root / "generation-source.txt"
            marker.write_text("v1\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "generation"], check=True)
            generation = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()

            for relative in sorted(EXPECTED_NON_GENERATION_PATHS):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "release controls"], check=True)
            validation = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()

            changed = MODULE.validate_gate_revision(root, generation, validation)
            self.assertEqual(set(changed), EXPECTED_NON_GENERATION_PATHS)

            generation_input = root / "ansible/arm64/roles/rpi5_common_kernel_build/tasks/main.yml"
            generation_input.parent.mkdir(parents=True, exist_ok=True)
            generation_input.write_text("generation-change\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "generation change"], check=True)
            unsafe = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()

            with self.assertRaises(MODULE.GateError) as context:
                MODULE.validate_gate_revision(root, generation, unsafe)
            self.assertEqual(context.exception.reason, "generation_source_changed_since_observer")


if __name__ == "__main__":
    unittest.main()
