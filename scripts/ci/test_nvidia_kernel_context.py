#!/usr/bin/env python3
"""Render the real role shell and exercise its make handoff without a live kernel."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "ansible/arm64/roles/rpi5_common_kernel_build/tasks/main.yml"
CLEAN = "Rpi5 common kernel NVIDIA external modules worktree を clean にする"
BUILD = "Rpi5 common kernel 向け NVIDIA external modules を build"
RELEASE = "fixture-target-v8-homecluster+"


class NvidiaKernelContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = self.root / "target-kernel"
        self.modules = self.root / "open-modules"
        self.record = self.root / "calls"
        self.kernel.mkdir()
        self.modules.mkdir()
        (self.kernel / ".config").write_text("CONFIG_MODULES=y\n")
        # A small Kbuild stand-in records the consumer's source/output/release.
        # No /lib/modules tree is created or accessed by the fixture.
        (self.kernel / "Makefile").write_text(
            f"kernelrelease:\n\t@echo '{RELEASE}'\n"
            "clean modules:\n"
            "\t@test \"$(SYSOUT)\" = \"$(CURDIR)\"\n"
            f"\t@test \"$(KERNEL_UNAME)\" = '{RELEASE}'\n"
            f"\t@printf '%s\\n' '$@' >> '{self.record}'\n"
        )
        # NVIDIA defaults to the running kernel if SYSSRC is missing. This
        # deliberately nonexistent fallback makes the regression observable.
        (self.modules / "Makefile").write_text(
            "SYSSRC ?= /nonexistent-running-kernel/build\n"
            "clean modules:\n\t@$(MAKE) --no-print-directory -C \"$(SYSSRC)\" $@\n"
        )
        for args in (("init", "-q"), ("add", "Makefile"),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.modules), *args], check=True,
                           capture_output=True)
        revision = subprocess.check_output(
            ["git", "-C", str(self.modules), "rev-parse", "HEAD"], text=True).strip()
        self.values = {
            "rpi5_common_kernel_build_dir": str(self.kernel),
            "rpi5_common_kernel_build_open_modules_dir": str(self.modules),
            "rpi5_common_kernel_build_open_modules_commit": revision,
            "rpi5_common_kernel_build_kernelrelease_command": {"stdout": RELEASE},
            "rpi5_common_kernel_build_nvidia_module_jobs": 1,
        }
        self.loader = DataLoader()
        self.tasks = self.loader.load_from_file(str(TASKS))

    def run_task(self, name):
        task = next(t for t in self.tasks if t.get("name") == name)
        shell = Templar(loader=self.loader, variables=self.values).template(
            trust_as_template(str(task["ansible.builtin.shell"])))
        env = dict(os.environ)
        # Do not let the test host's make configuration supply missing pins.
        for key in ("SYSSRC", "SYSOUT", "KERNEL_UNAME", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
            env.pop(key, None)
        return subprocess.run(["bash", "-c", shell], text=True, capture_output=True,
                              env=env, timeout=20)

    def test_clean_and_build_use_the_same_target_kernel(self):
        (self.modules / "stale.o").write_text("previous generation")
        for name in (CLEAN, BUILD):
            result = self.run_task(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record.read_text().splitlines(), ["clean", "modules"])
        self.assertFalse((self.modules / "stale.o").exists())
        self.assertTrue((self.kernel / ".config").is_file())

    def test_missing_target_config_rejected_before_worktree_cleanup(self):
        (self.kernel / ".config").unlink()
        sentinel = self.modules / "stale.o"
        sentinel.write_text("preserve on invalid input")
        self.assertNotEqual(self.run_task(CLEAN).returncode, 0)
        self.assertTrue(sentinel.exists())
        self.assertFalse(self.record.exists())

    def test_wrong_target_release_rejected_before_cleanup(self):
        self.values["rpi5_common_kernel_build_kernelrelease_command"] = {"stdout": "wrong"}
        sentinel = self.modules / "stale.o"
        sentinel.write_text("preserve on invalid input")
        self.assertNotEqual(self.run_task(CLEAN).returncode, 0)
        self.assertTrue(sentinel.exists())
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
