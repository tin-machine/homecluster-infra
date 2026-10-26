#!/usr/bin/env python3
"""Verify that Ansible's shallow Git checkout honors a resolved commit after master moves."""

from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
BUILD_TASKS = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"


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

            tasks = [task for task in yaml.safe_load(BUILD_TASKS.read_text())
                     if task.get("name") in {
                         "PXE paired build の Portage revision を取得",
                         "PXE paired build の firmware revision を取得",
                     }]
            self.assertEqual(len(tasks), 2)
            tasks[0]["ansible.builtin.command"]["argv"][2] = remote.as_uri()
            tasks.append({"ansible.builtin.assert": {"that": [
                f"pxe_release_bundle_portage_remote.stdout.split()[0] == '{fixed}'",
                f"pxe_release_bundle_firmware_remote.stdout.split()[0] == '{fixed}'",
            ]}})
            playbook = [{
                "hosts": "buildhost", "gather_facts": False,
                "vars": {
                    "ansible_connection": "ssh", "ansible_user": "root",
                    "pxe_release_bundle_skip_build": False,
                    "openwrt_gentoo_portage_revision": "master",
                    "openwrt_gentoo_copy_rpi_modules": True,
                    "openwrt_gentoo_rpi_firmware_version": "master",
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

            playbook = [{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "tasks": [{"ansible.builtin.git": {
                    "repo": remote.as_uri(), "dest": str(root / "checkout"),
                    "version": fixed, "depth": 1, "update": True, "force": True,
                }}],
            }]
            path = root / "pin.yml"
            path.write_text(yaml.safe_dump(playbook, sort_keys=False))
            for _ in range(2):
                result = subprocess.run(
                    ["ansible-playbook", "-i", "localhost,", str(path)],
                    capture_output=True, text=True, timeout=45,
                )
                self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])
                self.assertEqual(git(root / "checkout", "rev-parse", "HEAD"), fixed)
                self.assertEqual((root / "checkout/input").read_text(), "first\n")


if __name__ == "__main__":
    unittest.main()
