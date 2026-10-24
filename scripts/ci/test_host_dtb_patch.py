#!/usr/bin/env python3
"""Exercise the host-local DTB patch with dated release fixtures."""

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
HELPER = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/files/patch_host_dtb.py"
SPEC = importlib.util.spec_from_file_location("patch_host_dtb", HELPER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HostDtbPatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.release = root / "dates/20261106-rpi5/bcm2712-rpi-5-b.dtb"
        self.release.parent.mkdir(parents=True)
        self.release.write_bytes(b"prefix cgroup_disable=memory suffix")
        self.host = root / "board/bcm2712-rpi-5-b.dtb"
        self.host.parent.mkdir()
        self.host.symlink_to("../dates/20261106-rpi5/bcm2712-rpi-5-b.dtb")

    def test_shared_release_remains_unchanged_and_rerun_is_idempotent(self):
        original = self.release.read_bytes()
        self.assertTrue(MODULE.patch(self.release, self.host))
        self.assertFalse(self.host.is_symlink())
        self.assertNotIn(MODULE.NEEDLE, self.host.read_bytes())
        self.assertEqual(self.release.read_bytes(), original)
        self.assertFalse(MODULE.patch(self.release, self.host))

    def test_next_release_replaces_host_copy(self):
        MODULE.patch(self.release, self.host)
        next_release = self.release.parent.parent / "20261107-rpi5" / self.release.name
        next_release.parent.mkdir()
        next_release.write_bytes(b"next cgroup_disable=memory")
        self.assertTrue(MODULE.patch(next_release, self.host))
        self.assertEqual(self.host.read_bytes(), b"next " + b" " * len(MODULE.NEEDLE))
        self.assertIn(MODULE.NEEDLE, next_release.read_bytes())

    def test_unexpected_symlink_is_rejected(self):
        self.host.unlink()
        self.host.symlink_to(self.release)
        with self.assertRaisesRegex(ValueError, "unexpected host DTB symlink"):
            MODULE.patch(self.release, self.host)
        self.assertIn(MODULE.NEEDLE, self.release.read_bytes())

    def test_legacy_date_only_release_symlink_is_accepted(self):
        self.host.unlink()
        self.host.symlink_to("../dates/20261106/bcm2712-rpi-5-b.dtb")
        self.assertTrue(MODULE.patch(self.release, self.host))
        self.assertFalse(self.host.is_symlink())

    def test_symlink_to_previous_named_release_is_accepted(self):
        self.host.unlink()
        self.host.symlink_to("../dates/20261105-rpi5/bcm2712-rpi-5-b.dtb")
        self.assertTrue(MODULE.patch(self.release, self.host))
        self.assertFalse(self.host.is_symlink())

    def test_symlink_outside_release_directory_is_rejected(self):
        self.host.unlink()
        self.host.symlink_to("../other/20261106-rpi5/bcm2712-rpi-5-b.dtb")
        with self.assertRaisesRegex(ValueError, "unexpected host DTB symlink"):
            MODULE.patch(self.release, self.host)

    def test_missing_release_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "release DTB is not a regular file"):
            MODULE.patch(self.release.parent / "missing.dtb", self.host)


if __name__ == "__main__":
    unittest.main()
