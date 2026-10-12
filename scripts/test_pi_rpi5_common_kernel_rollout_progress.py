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
    def test_atomic_private_sidecar_and_heartbeat_preserve_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = rollout.ProgressWriter(Path(temporary), "full_fleet")
            stderr = StringIO()
            with redirect_stderr(stderr):
                writer.start()
                first = json.loads(writer.path.read_text(encoding="utf-8"))
                writer.transition("selector_apply", "common_kernel_selector_apply", "started")
                transition = json.loads(writer.path.read_text(encoding="utf-8"))
                writer._emit(heartbeat=True)
                heartbeat = json.loads(writer.path.read_text(encoding="utf-8"))
                writer.finish("fail")
            terminal = json.loads(writer.path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(writer.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(writer.path.parent.stat().st_mode), 0o700)
            self.assertEqual(first["phase"], "accepted")
            self.assertEqual(transition["phase"], "selector_apply")
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
