#!/usr/bin/env python3
"""Synthetic symlink/directory and malformed-selector contract checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

PROBE = Path(__file__).resolve().parents[1] / "pxe_live_selectors_readonly_status.py"
spec = importlib.util.spec_from_file_location("selector_probe", PROBE)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
HASHES = [f"{n:08x}" for n in range(1, 5)]


class SelectorTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        for n, board_hash in enumerate(HASHES):
            release = "20261023-rpi4" if n == 3 else "20261023-rpi5"
            date = root / "dates" / release
            date.mkdir(parents=True, exist_ok=True)
            (date / "cmdline.txt").write_text(
                f"console=tty1 root=nfs4:host:/gentoo/{release}/nfs,vers=4.2 rw\n",
                encoding="utf-8",
            )
            (root / board_hash).symlink_to(f"dates/{release}")

    def test_four_symlinks_observed_without_hashes_in_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            result = probe.inspect(HASHES, root=root)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["selectors"]), 4)
        self.assertNotIn(HASHES[0], str(result))

    def test_directory_mode_and_rootfs_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            host = root / HASHES[0]
            host.unlink()
            host.mkdir()
            (host / "kernel8.img").symlink_to("../dates/20261023-rpi5/kernel8.img")
            (host / "cmdline.txt").write_text(
                "root=nfs4:host:/gentoo/20261022-rpi5/nfs,vers=4.2\n", encoding="utf-8",
            )
            result = probe.inspect(HASHES, root=root)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["selectors"][0]["rootfs_release"], "20261022-rpi5")

    def test_invalid_path_or_architecture_is_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / HASHES[0]).unlink()
            (root / HASHES[0]).symlink_to("../../secret")
            self.assertEqual(probe.inspect(HASHES, root=root)["status"], "unknown")
            (root / HASHES[0]).unlink()
            (root / HASHES[0]).symlink_to("dates/20261023-rpi4")
            (root / "dates/20261023-rpi4/cmdline.txt").write_text(
                "root=nfs4:host:/gentoo/20261023-rpi5/nfs,vers=4.2\n", encoding="utf-8",
            )
            self.assertEqual(probe.inspect(HASHES, root=root)["reason"], "selector_architecture_mismatch")
        self.assertEqual(probe.inspect(["../bad"] * 4)["reason"], "board_hashes_invalid")


if __name__ == "__main__":
    unittest.main()
