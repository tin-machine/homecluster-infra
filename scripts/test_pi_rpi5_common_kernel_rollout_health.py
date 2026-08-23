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

    def test_healthy_requires_zero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            with mock.patch.object(ROLLOUT_MODULE, "run", return_value=self.completed("healthy", 0, nodes_ready=2, nodes_total=2, issues=[])):
                ROLLOUT_MODULE.ensure_cluster_healthy(runbook)

    def test_converging_preserves_semantic_status_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            with mock.patch.object(ROLLOUT_MODULE, "run", return_value=self.completed("converging", 1, nodes_ready=1, nodes_total=4)):
                with self.assertRaises(ROLLOUT_MODULE.RolloutError) as context:
                    ROLLOUT_MODULE.ensure_cluster_healthy(runbook)
            self.assertEqual(context.exception.reason, "rollout_cluster_converging")
            self.assertEqual(context.exception.status, "blocked")
            self.assertIn("k3s_nodes_ready=1", context.exception.diagnostics)
            self.assertIn("k3s_nodes_total=4", context.exception.diagnostics)
            self.assertIn("k3s_issues=nodes_not_ready", context.exception.diagnostics)

    def test_unknown_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            with mock.patch.object(ROLLOUT_MODULE, "run", return_value=self.completed("unknown", 2)):
                with self.assertRaises(ROLLOUT_MODULE.RolloutError) as context:
                    ROLLOUT_MODULE.ensure_cluster_healthy(runbook)
            self.assertEqual(context.exception.reason, "rollout_cluster_unknown")
            self.assertEqual(context.exception.status, "unknown")

    def test_healthy_nonzero_exit_is_contract_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            runbook = self.runbook(Path(temporary))
            with mock.patch.object(ROLLOUT_MODULE, "run", return_value=self.completed("healthy", 1, nodes_ready=2, nodes_total=2, issues=[])):
                with self.assertRaises(ROLLOUT_MODULE.RolloutError) as context:
                    ROLLOUT_MODULE.ensure_cluster_healthy(runbook)
            self.assertEqual(context.exception.reason, "rollout_k3s_status_exit_mismatch")
            self.assertEqual(context.exception.status, "unknown")
            self.assertIn("k3s_exit_code=1", context.exception.diagnostics)


if __name__ == "__main__":
    unittest.main()
