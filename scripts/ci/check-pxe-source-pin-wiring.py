#!/usr/bin/env python3
"""Run source-pin Ansible tasks against two temporary rootfs trees only.

The synthetic role preserves task logic; root ownership fields are removed for
unprivileged execution and checked separately against the authoritative source.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs"


class WiringTests(unittest.TestCase):
    def source_tasks(self):
        path = ROLE / "tasks/source_pin.yml"
        self.assertTrue(path.is_file(), "source_pin tasks have not been implemented")
        tasks = yaml.safe_load(path.read_text())
        self.assertIsInstance(tasks, list)
        return tasks

    def run_role(self, *, mode="pinned", changes=None, stale=False, expect_ok=True):
        tasks = self.source_tasks()
        with tempfile.TemporaryDirectory(prefix="homecluster-pin-wiring-") as temporary:
            root = Path(temporary)
            roots = [root / "pi4", root / "pi5"]
            for destination in roots:
                destination.mkdir()
                if stale:
                    target = destination / "etc/ansible-pull/source-declaration.json"
                    target.parent.mkdir(parents=True)
                    if stale == "symlink":
                        target.symlink_to("missing-declaration.json")
                    else:
                        target.write_bytes(b'{"stale": true}\n')
            copied = root / "roles/openwrt_gentoo_rootfs"
            for directory in ("defaults", "files", "tasks", "templates"):
                (copied / directory).mkdir(parents=True)
            shutil.copy(ROLE / "defaults/main.yml", copied / "defaults/main.yml")
            shutil.copy(
                ROLE / "files/pxe-source-pin.py", copied / "files/pxe-source-pin.py"
            )
            for template in (ROLE / "templates").glob("*source*declaration*"):
                shutil.copy(template, copied / "templates" / template.name)

            def unprivileged(value):
                if isinstance(value, dict):
                    return {
                        key: unprivileged(item)
                        for key, item in value.items()
                        if key not in ("owner", "group")
                    }
                if isinstance(value, list):
                    return [unprivileged(item) for item in value]
                return value

            (copied / "tasks/source_pin.yml").write_text(
                yaml.safe_dump(unprivileged(tasks))
            )
            values = {
                "openwrt_gentoo_enabled": True,
                "openwrt_gentoo_ansible_pull_enabled": True,
                "openwrt_gentoo_rootfs_dir": str(roots[0]),
                "openwrt_gentoo_ansible_pull_rootfs_dirs": [str(p) for p in roots],
                "openwrt_gentoo_ansible_pull_env_group": "env_stg",
                "openwrt_gentoo_ansible_pull_source_mode": mode,
                "openwrt_gentoo_ansible_pull_source_environment": "staging",
                "openwrt_gentoo_ansible_pull_source_revision": "a" * 40,
                "openwrt_gentoo_ansible_pull_source_build_id": "20261018T000000Z-fixture-1",
            }
            if changes:
                values.update(changes)
            play = [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "become": False,
                    "vars": values,
                    "tasks": [
                        {
                            "ansible.builtin.include_role": {
                                "name": "openwrt_gentoo_rootfs",
                                "tasks_from": "source_pin",
                            }
                        }
                    ],
                }
            ]
            play_path = root / "test.yml"
            play_path.write_text(yaml.safe_dump(play))
            config = root / "ansible.cfg"
            config.write_text("[defaults]\nretry_files_enabled=False\n")
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(root),
                "LANG": "C.UTF-8",
                "ANSIBLE_CONFIG": str(config),
                "ANSIBLE_ROLES_PATH": str(root / "roles"),
                "ANSIBLE_LOCAL_TEMP": str(root / "local-tmp"),
                "ANSIBLE_REMOTE_TEMP": str(root / "remote-tmp"),
                "ANSIBLE_NOCOLOR": "1",
            }
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(play_path)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if not expect_ok:
                self.assertNotEqual(result.returncode, 0)
                for destination in roots:
                    target = destination / "etc/ansible-pull/source-declaration.json"
                    if stale == "symlink":
                        self.assertTrue(target.is_symlink())
                        self.assertEqual(os.readlink(target), "missing-declaration.json")
                    elif stale:
                        self.assertEqual(target.read_bytes(), b'{"stale": true}\n')
                    else:
                        self.assertFalse(os.path.lexists(target))
                    self.assertFalse((destination / "usr/local/libexec/pxe-source-pin.py").exists())
                return
            self.assertEqual(
                result.returncode, 0, result.stdout[-2500:] + result.stderr[-1000:]
            )
            for destination in roots:
                helper = destination / "usr/local/libexec/pxe-source-pin.py"
                self.assertEqual(
                    helper.read_bytes(), (ROLE / "files/pxe-source-pin.py").read_bytes()
                )
                self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
                target = destination / "etc/ansible-pull/source-declaration.json"
                if mode == "legacy":
                    self.assertFalse(target.exists())
                    continue
                raw = target.read_bytes()
                self.assertTrue(raw.endswith(b"\n"))
                self.assertEqual(target.stat().st_mode & 0o777, 0o644)
                self.assertEqual(
                    json.loads(raw),
                    {
                        "schema": "homecluster-pxe-source-declaration-v1",
                        "environment": "staging",
                        "infra_revision": "a" * 40,
                        "build_id": values[
                            "openwrt_gentoo_ansible_pull_source_build_id"
                        ],
                    },
                )
                spec = importlib.util.spec_from_file_location(
                    "fixture_source_pin", helper
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(
                    module.decode_declaration(raw, stage="stg")["infra_revision"],
                    "a" * 40,
                )
            if mode == "pinned":
                self.assertEqual(
                    (
                        roots[0] / "etc/ansible-pull/source-declaration.json"
                    ).read_bytes(),
                    (
                        roots[1] / "etc/ansible-pull/source-declaration.json"
                    ).read_bytes(),
                )

    def test_pinned_two_rootfs_declarations_and_helpers(self):
        self.run_role()

    def test_legacy_installs_helper_without_declaration(self):
        self.run_role(mode="legacy")

    def test_legacy_does_not_silently_delete_old_pin(self):
        self.run_role(mode="legacy", stale=True, expect_ok=False)

    def test_legacy_rejects_dangling_declaration_symlink(self):
        self.run_role(mode="legacy", stale="symlink", expect_ok=False)

    def test_bad_identity_never_writes_declaration(self):
        cases = [
            {"openwrt_gentoo_ansible_pull_source_mode": "auto"},
            {"openwrt_gentoo_ansible_pull_source_revision": "stg"},
            {"openwrt_gentoo_ansible_pull_source_build_id": "../escape"},
            {"openwrt_gentoo_ansible_pull_source_environment": "production"},
            {"openwrt_gentoo_ansible_pull_env_group": "env_prd"},
            {"openwrt_gentoo_ansible_pull_source_revision": True},
            {"openwrt_gentoo_ansible_pull_source_revision": "a" * 40 + "\n"},
            {"openwrt_gentoo_ansible_pull_source_build_id": 123},
            {"openwrt_gentoo_ansible_pull_source_build_id": "valid\n"},
            {"openwrt_gentoo_ansible_pull_source_build_id": "a" * 129},
        ]
        for change in cases:
            with self.subTest(change=change):
                self.run_role(changes=change, expect_ok=False)

    def test_root_ownership_and_safe_default(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        self.assertEqual(
            defaults.get("openwrt_gentoo_ansible_pull_source_mode"), "legacy"
        )
        for name in ("environment", "revision", "build_id"):
            self.assertEqual(defaults.get("openwrt_gentoo_ansible_pull_source_" + name), "")
        def walk(tasks):
            for task in tasks:
                yield task
                for key in ("block", "rescue", "always"):
                    yield from walk(task.get(key, []))

        writes = 0
        for task in walk(self.source_tasks()):
            for key in ("ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.file"):
                if key in task:
                    self.assertEqual(task[key].get("owner"), "root")
                    self.assertEqual(task[key].get("group"), "root")
                    writes += key in ("ansible.builtin.copy", "ansible.builtin.template")
        self.assertGreaterEqual(writes, 2)

    def test_wiring_precedes_wrapper_and_preserves_tags(self):
        tasks = yaml.safe_load((ROLE / "tasks/pxe_runtime.yml").read_text())
        includes = [
            (i, t)
            for i, t in enumerate(tasks)
            if isinstance(t.get("ansible.builtin.include_tasks"), dict)
            and t["ansible.builtin.include_tasks"].get("file")
            == "source_pin.yml"
        ]
        self.assertEqual(len(includes), 1)
        wrapper = next(
            i
            for i, t in enumerate(tasks)
            if t.get("ansible.builtin.template", {}).get("src")
            == "pxe-ansible-pull-wrapper.sh.j2"
        )
        index, task = includes[0]
        self.assertLess(index, wrapper)
        roots_ready = next(
            i for i, item in enumerate(tasks)
            if "openwrt_gentoo_ansible_pull_rootfs_dirs" in item.get("ansible.builtin.set_fact", {})
        )
        self.assertLess(roots_ready, index)
        self.assertTrue({"openwrt_gentoo_enabled | bool", "openwrt_gentoo_ansible_pull_enabled | bool"} <= set(task.get("when", [])))
        self.assertTrue({"pxe", "pxe_ansible_pull"} <= set(task.get("tags", [])))
        self.assertTrue(
            {"pxe", "pxe_ansible_pull"}
            <= set(
                task["ansible.builtin.include_tasks"].get("apply", {}).get("tags", [])
            )
        )


if __name__ == "__main__":
    unittest.main()
