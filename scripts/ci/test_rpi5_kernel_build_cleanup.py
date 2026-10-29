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

    def test_postcomplete_protects_current_rollback_and_module_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            labels = (
                "20260602-rpi5-nvidia", "20261024-rpi5-homecluster",
                "20261025-rpi5-homecluster", "20261026-rpi5-homecluster",
                "20261027-rpi5-homecluster",
            )
            for label in labels:
                (root / label).mkdir()
                (root / label / "build.o").write_bytes(b"build")
            modules = Path(tmp) / "modules" / "6.18.36"
            modules.mkdir(parents=True)
            (modules / "build").symlink_to(root / "20261025-rpi5-homecluster" / "linux")
            mountinfo = Path(tmp) / "mountinfo"
            mountinfo.write_text("1 0 0:1 / / rw - ext4 /dev/a rw\n")
            plan = cleanup.build_plan(
                root,
                {"20261024-rpi5-homecluster", "20261026-rpi5-homecluster"},
                1, 0, proc_root=root, post_complete=True,
                modules_root=modules.parent, mountinfo=mountinfo,
            )
            self.assertEqual(plan["delete_labels"], [
                "20260602-rpi5-nvidia", "20261027-rpi5-homecluster",
            ])
            self.assertIn("20261025-rpi5-homecluster", plan["protected_labels"])

    def test_postcomplete_blocks_mounted_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            old = root / "20261024-rpi5-homecluster"
            old.mkdir()
            modules = Path(tmp) / "modules" / "current"
            modules.mkdir(parents=True)
            (modules / "build").symlink_to(root / "20261026-rpi5-homecluster" / "linux")
            mountinfo = Path(tmp) / "mountinfo"
            mountinfo.write_text(f"1 0 0:1 / {old / 'staging'} rw - ext4 /dev/a rw\n")
            with self.assertRaisesRegex(cleanup.CleanupBlocked, "candidate_contains_mount"):
                cleanup.build_plan(
                    root, {"20261026-rpi5-homecluster"}, 1, 0,
                    proc_root=root, post_complete=True, modules_root=modules.parent,
                    mountinfo=mountinfo,
                )

    def test_postcomplete_rejects_unmounted_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kernel-build"
            root.mkdir()
            args = [
                "cleanup_old_builds.py", "--root", str(root),
                "--accepted", "20261026-rpi5-homecluster",
                "--protected-label", "20261024-rpi5-homecluster",
                "--post-complete",
            ]
            with patch.object(cleanup, "PRODUCTION_ROOT", root), patch.object(
                cleanup, "separate_work_mount", return_value=False
            ), patch.object(
                cleanup.sys, "argv", args
            ), patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(cleanup.main(), 2)
            self.assertEqual(json.loads(out.getvalue())["reason"], "work_root_not_separate_mount")


if __name__ == "__main__":
    unittest.main()
