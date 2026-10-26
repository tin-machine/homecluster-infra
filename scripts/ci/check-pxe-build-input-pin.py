#!/usr/bin/env python3
"""Verify that pinned PXE inputs stay shallow after the upstream branch moves."""

from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
BUILD_TASKS = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"
FIRMWARE_TASKS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/rpi_firmware_modules.yml"
PORTAGE_TASKS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/portage_chroot.yml"


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


class BuildInputPinTests(unittest.TestCase):
    def test_delegated_input_resolution_overrides_openwrt_ssh_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "master", str(source)], check=True)
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            (source / "input").write_text("fixed\n")
            git(source, "add", "input")
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixed"], check=True)
            git(source, "remote", "add", "origin", str(remote))
            git(source, "push", "-q", "origin", "master")
            fixed = git(source, "rev-parse", "HEAD")
            remote_tmp_file = root / "openwrt-remote-tmp-is-not-a-directory"
            remote_tmp_file.write_text("not a directory\n")

            tasks = [task for task in yaml.safe_load(BUILD_TASKS.read_text())
                     if task.get("name") in {
                         "PXE paired build の Portage revision を取得",
                         "PXE paired build の firmware revision を取得",
                         "PXE paired build の moving input を commit に固定",
                         "PXE paired build の input revision を検証",
                     }]
            self.assertEqual(len(tasks), 4)
            tasks[0]["ansible.builtin.command"]["argv"][2] = remote.as_uri()
            tasks.append({"ansible.builtin.assert": {"that": [
                f"pxe_release_bundle_portage_revision == '{fixed}'",
                f"pxe_release_bundle_firmware_revision == '{fixed}'",
            ]}})
            playbook = [{
                "hosts": "buildhost", "gather_facts": False,
                "vars": {
                    "ansible_connection": "ssh", "ansible_user": "root",
                    "ansible_remote_tmp": str(remote_tmp_file),
                    "pxe_release_bundle_skip_build": False,
                    "openwrt_gentoo_rpi_firmware_repo": remote.as_uri(),
                },
                "tasks": tasks,
            }]
            path = root / "delegation.yml"
            path.write_text(yaml.safe_dump(playbook, sort_keys=False))
            result = subprocess.run(
                ["ansible-playbook", "-i", "buildhost,", str(path)],
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])

    def test_resolved_commit_survives_upstream_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            remote = root / "remote.git"
            source.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "master", str(source)], check=True)
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            git(source, "remote", "add", "origin", str(remote))
            (source / "input").write_text("first\n")
            git(source, "add", "input")
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "first"], check=True)
            git(source, "push", "-q", "origin", "master")
            fixed = subprocess.check_output(
                ["git", "ls-remote", remote.as_uri(), "refs/heads/master"], text=True
            ).split()[0]
            (source / "input").write_text("second\n")
            git(source, "add", "input")
            subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "second"], check=True)
            git(source, "push", "-q", "origin", "master")

            build = next(task for task in yaml.safe_load(BUILD_TASKS.read_text())
                         if task.get("name") == "PXE release bundle release を build")
            for kind, path, name, pin_var, version_var in (
                ("firmware", FIRMWARE_TASKS, "Raspberry Pi firmware リポジトリを取得",
                 "openwrt_gentoo_rpi_firmware_pinned_refspec", "openwrt_gentoo_rpi_firmware_version"),
                ("portage", PORTAGE_TASKS, "Portage tree を git で初期取得",
                 "openwrt_gentoo_portage_pinned_refspec", "openwrt_gentoo_portage_revision"),
            ):
                self.assertEqual(build["vars"][pin_var],
                                 "{{ pxe_release_bundle_" + kind + "_revision }}" if kind == "portage"
                                 else "{{ pxe_release_bundle_firmware_revision }}")
                source = yaml.safe_load(path.read_text())
                candidates = source if kind == "firmware" else source[0]["block"]
                task = deepcopy(next(item for item in candidates if item.get("name") == name))
                task.pop("when", None)
                options = task["ansible.builtin.git"]
                self.assertIn(pin_var, options["refspec"])
                options["repo"] = remote.as_uri()
                checkout = root / kind
                options["dest"] = str(checkout)
                playbook = [{
                    "hosts": "localhost", "connection": "local", "gather_facts": False,
                    "vars": {version_var: fixed, pin_var: fixed,
                             "openwrt_gentoo_rpi_firmware_depth": 1,
                             "openwrt_gentoo_rpi_firmware_force": False},
                    "tasks": [task],
                }]
                fixture = root / (kind + "-pin.yml")
                fixture.write_text(yaml.safe_dump(playbook, sort_keys=False))
                for _ in range(2):
                    result = subprocess.run(
                        ["ansible-playbook", "-i", "localhost,", str(fixture)],
                        capture_output=True, text=True, timeout=45,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
                    self.assertEqual(git(checkout, "rev-parse", "HEAD"), fixed)
                    self.assertEqual((checkout / "input").read_text(), "first\n")
                    self.assertTrue((checkout / ".git/shallow").is_file())


if __name__ == "__main__":
    unittest.main()
