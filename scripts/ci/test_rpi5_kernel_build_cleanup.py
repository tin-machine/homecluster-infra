#!/usr/bin/env python3
"""Safety checks for the bounded staging kernel build cleanup."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ansible/arm64/roles/rpi5_common_kernel_build/files/cleanup_old_builds.py"
)
spec = importlib.util.spec_from_file_location("cleanup_old_builds", SOURCE)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class CleanupSafetyTest(unittest.TestCase):
    def test_oldest_unprotected_only_until_capacity_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            for label in ("20261019", "20261020", "20261021", "20261022", "20261023"):
                work = root / f"{label}-rpi5-homecluster"
                work.mkdir()
                (work / "build.o").write_bytes(b"build")
            protected = cleanup.validate_labels(
                "20261023-rpi5-homecluster",
                "20261022-rpi5-homecluster",
                "20261021-rpi5-homecluster",
            )
            size = cleanup.directory_bytes(root / "20261019-rpi5-homecluster")
            plan = cleanup.build_plan(root, protected, size, 0, proc_root=root)
            self.assertEqual(plan["delete_labels"], ["20261019-rpi5-homecluster"])
            self.assertNotIn("20261021-rpi5-homecluster", plan["delete_labels"])

    def test_missing_capacity_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            protected = cleanup.validate_labels(
                "20261023-rpi5-homecluster",
                "20261022-rpi5-homecluster",
                "20261021-rpi5-homecluster",
            )
            with self.assertRaisesRegex(cleanup.CleanupBlocked, "insufficient_reclaimable_space"):
                cleanup.build_plan(root, protected, 1024, 0, proc_root=root)
            (root / "20261019-rpi5-homecluster").symlink_to(tmp)
            with self.assertRaisesRegex(cleanup.CleanupBlocked, "dated_entry_not_directory"):
                cleanup.build_plan(root, protected, 1024, 0, proc_root=root)

    def test_active_reference_and_bad_protection_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            old = root / "20261019-rpi5-homecluster"
            old.mkdir()
            proc = Path(tmp) / "proc"
            (proc / "123" / "fd").mkdir(parents=True)
            (proc / "123" / "cwd").symlink_to(old, target_is_directory=True)
            protected = cleanup.validate_labels(
                "20261023-rpi5-homecluster",
                "20261022-rpi5-homecluster",
                "20261021-rpi5-homecluster",
            )
            with self.assertRaisesRegex(cleanup.CleanupBlocked, "active_build_reference"):
                cleanup.build_plan(root, protected, 1024, 0, proc_root=proc)
            with self.assertRaisesRegex(cleanup.CleanupBlocked, "protected_labels_not_distinct"):
                cleanup.validate_labels(
                    "20261023-rpi5-homecluster",
                    "20261022-rpi5-homecluster",
                    "20261022-rpi5-homecluster",
                )

    def test_apply_requires_same_plan_and_preserves_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            old = root / "20261019-rpi5-homecluster"
            old.mkdir()
            (old / "build.o").write_bytes(b"old")
            current = root / "20261022-rpi5-homecluster"
            current.mkdir()
            args = [
                "cleanup_old_builds.py", "--root", str(root),
                "--candidate", "20261023-rpi5-homecluster",
                "--accepted", "20261022-rpi5-homecluster",
                "--rollback", "20261021-rpi5-homecluster",
                "--min-free-gib", "1",
            ]
            free = 1024**3 - cleanup.directory_bytes(old)
            usage = type("Usage", (), {"free": free})()
            with patch.object(cleanup, "PRODUCTION_ROOT", root), patch.object(
                cleanup.shutil, "disk_usage", return_value=usage
            ), patch.object(cleanup.sys, "argv", args), patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(cleanup.main(), 0)
                digest = json.loads(out.getvalue())["plan_sha256"]
            with patch.object(cleanup, "PRODUCTION_ROOT", root), patch.object(
                cleanup.shutil, "disk_usage", side_effect=[usage, type("Usage", (), {"free": 1024**3})()]
            ), patch.object(cleanup.sys, "argv", args + ["--apply", "--expected-plan-sha256", digest]), patch(
                "sys.stdout", new_callable=io.StringIO
            ):
                self.assertEqual(cleanup.main(), 0)
            self.assertFalse(old.exists())
            self.assertTrue(current.is_dir())


if __name__ == "__main__":
    unittest.main()
