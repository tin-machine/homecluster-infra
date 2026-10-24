#!/usr/bin/env python3
"""Exercise the PXE release producer and host DTB consumer together."""

import importlib.util
import pathlib
import shlex
import tempfile
import unittest

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs"
RELEASE_TASKS = ROLE / "tasks/pxe_host_releases.yml"
HOST_TASKS = ROLE / "tasks/tftp_hosts.yml"
DTB_HELPER = ROLE / "files/patch_host_dtb.py"
SPEC = importlib.util.spec_from_file_location("patch_host_dtb", DTB_HELPER)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def task_value(tasks, name, module, key=None):
    task = next(task for task in tasks if task.get("name") == name)
    value = task[module]
    return value[key] if key is not None else value


def render(loader, value, variables):
    return Templar(loader=loader, variables=variables).template(trust_as_template(str(value)))


class PxeHostDtbContractTest(unittest.TestCase):
    def test_generated_release_and_symlink_are_accepted_by_helper(self):
        loader = DataLoader()
        release_tasks = loader.load_from_file(str(RELEASE_TASKS))
        host_tasks = loader.load_from_file(str(HOST_TASKS))
        release_fact = task_value(
            release_tasks,
            "PXE host release を stage date から解決",
            "ansible.builtin.set_fact",
            "openwrt_pxe_hosts_effective",
        )
        host_fact = task_value(
            host_tasks,
            "TFTP hosts 用ボードハッシュ情報を整形",
            "ansible.builtin.set_fact",
            "openwrt_gentoo_tftp_host_entries",
        )
        symlink_script = task_value(
            host_tasks,
            "TFTP hosts の共通ファイル symlink を作成",
            "ansible.builtin.raw",
        )
        target_line = next(
            line.strip() for line in str(symlink_script).splitlines() if line.strip().startswith("target=")
        )

        cases = (
            ("rpi4-fixture", "20261107-rpi4", "bcm2711-rpi-4-b.dtb", ""),
            ("rpi5-fixture", "20261107-rpi5", "bcm2712-rpi-5-b.dtb", ""),
            ("rpi5-explicit", "20261027-prod-rpi5", "bcm2712-rpi-5-b.dtb", "20261027-prod-rpi5"),
        )
        for host_name, expected_release, dtb_name, explicit_release in cases:
            with self.subTest(host=host_name):
                host = {"name": host_name, "board_hash": "fixture-board", "tftp_stage": "stg"}
                if explicit_release:
                    host["tftp_release"] = explicit_release
                variables = {
                    "openwrt_pxe_hosts_effective": [host],
                    "openwrt_gentoo_release_bundle_stage_dates": {"stg": "20261107"},
                    "openwrt_gentoo_release_bundle_generations": [
                        {"name": "rpi4", "host_name_regex": "^rpi4-", "release_suffix": "rpi4"},
                        {"name": "rpi5", "host_name_regex": "^rpi5-", "release_suffix": "rpi5"},
                    ],
                }
                resolved = render(loader, release_fact, variables)
                self.assertEqual(resolved[0]["tftp_release"], expected_release)
                variables["openwrt_pxe_hosts_effective"] = resolved
                entry = render(loader, host_fact, variables)[0]
                self.assertEqual(entry["release"], expected_release)
                self.assertIn(dtb_name, entry["symlinks"])

                assignment = shlex.split(render(loader, target_line, {"item": (entry, dtb_name)}))
                self.assertEqual(len(assignment), 1)
                self.assertTrue(assignment[0].startswith("target="))
                target = assignment[0].removeprefix("target=")

                with tempfile.TemporaryDirectory() as directory:
                    root = pathlib.Path(directory)
                    source = root / "dates" / expected_release / dtb_name
                    source.parent.mkdir(parents=True)
                    original = b"bootargs=cgroup_disable=memory quiet\0"
                    source.write_bytes(original)
                    dest = root / "fixture-board" / dtb_name
                    dest.parent.mkdir()
                    dest.symlink_to(target)
                    self.assertEqual(dest.resolve(), source)
                    self.assertTrue(HELPER.patch(source, dest))
                    self.assertFalse(dest.is_symlink())
                    self.assertNotIn(HELPER.NEEDLE, dest.read_bytes())
                    self.assertEqual(source.read_bytes(), original)
                    self.assertFalse(HELPER.patch(source, dest))


if __name__ == "__main__":
    unittest.main()
