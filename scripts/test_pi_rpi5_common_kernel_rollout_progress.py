from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("pi-rpi5-common-kernel-rollout")
loader = importlib.machinery.SourceFileLoader("rollout_progress_test", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec and spec.loader
rollout = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rollout
spec.loader.exec_module(rollout)


class ProgressTests(unittest.TestCase):
    def test_progress_binds_generation_and_deployment_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generation = "20261021T232512Z-stg-generation-1708165"
            deployment = "deployment-" + "a" * 32
            writer = rollout.ProgressWriter(
                Path(temporary), "full_fleet",
                generation_run_id=generation, deployment_attempt_id=deployment,
            )
            with redirect_stderr(StringIO()):
                writer.start()
                writer.transition("fresh_boot", "common_kernel_fresh_boot", "started")
                writer.finish("pass")
            progress = json.loads(writer.path.read_text(encoding="utf-8"))
            self.assertEqual(progress["generation_run_id"], generation)
            self.assertEqual(progress["deployment_attempt_id"], deployment)
            self.assertEqual(progress["phase"], "terminal")

    def test_progress_rejects_malformed_or_unpaired_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for generation, deployment in (
                ("../other", ""),
                ("", "deployment-" + "a" * 32),
                ("20261021T232512Z-stg-generation-1708165", "deployment-bad"),
            ):
                with self.subTest(generation=generation, deployment=deployment):
                    with self.assertRaises(rollout.RolloutError):
                        rollout.ProgressWriter(
                            root, "full_fleet", generation_run_id=generation,
                            deployment_attempt_id=deployment,
                        )
            self.assertFalse((root / "progress/current.json").exists())

    def test_atomic_private_sidecar_and_heartbeat_preserve_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = rollout.ProgressWriter(Path(temporary), "full_fleet")
            stderr = StringIO()
            with redirect_stderr(stderr):
                writer.start()
                first = json.loads(writer.path.read_text(encoding="utf-8"))
                writer.transition(
                    "selector_apply", "common_kernel_selector_apply", "started",
                    mutation_started=None, mutation_committed=None,
                )
                transition = json.loads(writer.path.read_text(encoding="utf-8"))
                writer._emit(heartbeat=True)
                heartbeat = json.loads(writer.path.read_text(encoding="utf-8"))
                writer.finish("fail")
            terminal = json.loads(writer.path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(writer.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(writer.path.parent.stat().st_mode), 0o700)
            self.assertEqual(first["phase"], "accepted")
            self.assertEqual(transition["phase"], "selector_apply")
            self.assertIsNone(transition["mutation_started"])
            self.assertIsNone(transition["mutation_committed"])
            self.assertEqual(heartbeat["last_transition_at"], transition["last_transition_at"])
            self.assertEqual(terminal["attempt_id"], first["attempt_id"])
            self.assertTrue(terminal["terminal"])
            self.assertEqual(terminal["status"], "fail")
            self.assertIn("progress=heartbeat", stderr.getvalue())

    def test_live_preflight_failure_seals_progress_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stdout, stderr = StringIO(), StringIO()
            env = {
                "XDG_STATE_HOME": temporary,
                "HOMECLUSTER_RPI5_COMMON_KERNEL_ROLLOUT_APPLY": "1",
                "HOMECLUSTER_ANSIBLE_INVENTORY": str(Path(temporary) / "missing-inventory.yml"),
                rollout.EXPECTED_ACCEPTED_RUN_ENV: "20261021T232512Z-stg-generation-1708165",
                rollout.DEPLOYMENT_ATTEMPT_ENV: "deployment-" + "b" * 32,
            }
            with mock.patch.dict(os.environ, env), redirect_stdout(stdout), redirect_stderr(stderr):
                code = rollout.main(["--json", "--phase", "full_fleet"])
            result = json.loads(stdout.getvalue())
            progress = json.loads(
                (Path(temporary) / "homecluster/rpi5-common-kernel/progress/current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "inventory_entrypoint_invalid")
            self.assertTrue(progress["terminal"])
            self.assertEqual(progress["status"], "blocked")
            self.assertFalse(progress["mutation_started"])
            self.assertEqual(progress["generation_run_id"], env[rollout.EXPECTED_ACCEPTED_RUN_ENV])
            self.assertEqual(progress["deployment_attempt_id"], env[rollout.DEPLOYMENT_ATTEMPT_ENV])
            self.assertIn("phase=accepted", stderr.getvalue())
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)

    def test_fresh_boot_and_acceptance_boundaries_update_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = rollout.ProgressWriter(Path(temporary), "generic_canary")
            with redirect_stderr(StringIO()):
                writer.start()
                with mock.patch.object(rollout, "_PROGRESS", writer), mock.patch.object(
                    rollout, "_original_run_fresh_boot", return_value=("fixture-run", "pass")
                ), mock.patch.object(rollout, "_CURRENT_ACTION", "generic_canary"):
                    self.assertEqual(rollout.run_fresh_boot(Path(temporary)), ("fixture-run", "pass"))
                    boot = json.loads(writer.path.read_text(encoding="utf-8"))
                    self.assertEqual(boot["phase"], "fresh_boot")
                    self.assertTrue(boot["power_cycle_started"])
                    with mock.patch.object(rollout, "_run_phase_acceptance_impl", return_value=(True, [])):
                        accepted, _ = rollout.run_phase_acceptance(
                            Path(temporary), Path(temporary), Path(temporary),
                            "generic_canary", [], [], "fixture-release",
                        )
                    self.assertTrue(accepted)
                    postboot = json.loads(writer.path.read_text(encoding="utf-8"))
                    self.assertEqual(postboot["phase"], "postboot_acceptance")
                    self.assertEqual(postboot["check_status"], "pass")
                writer.finish("pass")


if __name__ == "__main__":
    unittest.main()
