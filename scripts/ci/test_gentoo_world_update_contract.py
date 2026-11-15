#!/usr/bin/env python3
"""Exercise the actual role-rendered service and its update helper offline."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/arm64/roles/common/emerge"
HELPER = ROLE / "files/gentoo-world-update.py"
REQUIRED = (
    "app-admin/ansible-core", "dev-vcs/git", "net-firewall/iptables",
    "net-firewall/nftables", "sys-fs/f2fs-tools",
)


class WorldUpdateContract(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("world_update_under_test", HELPER)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory(prefix="world-update-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.world = self.root / "world"
        self.world.write_text("# preserve\napp-admin/ansible\ndev-vcs/git\n")
        self.declaration = self.root / "source-declaration.json"
        self.commands = []
        self.fail_at = None

    def invoke(self, args=()):
        def command(argv, **kwargs):
            self.commands.append([str(x) for x in argv])
            if len(self.commands) == self.fail_at:
                raise subprocess.CalledProcessError(42, argv)
            return subprocess.CompletedProcess(argv, 0)

        output = io.StringIO()
        with mock.patch.object(self.module, "WORLD_FILE", self.world), \
                mock.patch.object(self.module, "SOURCE_DECLARATION", self.declaration), \
                mock.patch.object(self.module.subprocess, "run", side_effect=command), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = self.module.main(list(args))
        return result, output.getvalue()

    def service_args(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        service = next(t["ansible.builtin.copy"]["content"] for t in tasks
                       if t.get("name") == "Install gentoo world update systemd service")
        rendered = Environment(undefined=StrictUndefined).from_string(service).render(
            gentoo_world_update_exclude_args_resolved="--exclude=sys-devel/gcc")
        starts = [x.removeprefix("ExecStart=") for x in rendered.splitlines()
                  if x.startswith("ExecStart=")]
        self.assertEqual(len(starts), 1)
        argv = shlex.split(starts[0])
        self.assertEqual(argv[0], "/usr/local/sbin/gentoo-world-update")
        self.assertNotIn("/bin/sh", argv)
        self.assertNotIn("%s", rendered)
        copies = [t.get("ansible.builtin.copy", {}) for t in tasks]
        self.assertTrue(any(t.get("src") == "gentoo-world-update.py"
                            and t.get("dest") == argv[0]
                            and str(t.get("mode")) == "0755" for t in copies))
        return argv[1:]

    def test_role_to_helper_preserves_update_order_and_excludes(self):
        status, _ = self.invoke(self.service_args())
        self.assertEqual(status, 0)
        self.assertEqual(self.commands, [
            ["/usr/bin/emerge", "--sync"],
            ["/usr/bin/emerge", "--noreplace", *REQUIRED],
            ["/usr/bin/emerge", "--exclude=sys-devel/gcc", "-uDN", "--with-bdeps=y",
             "--ask=n", "--verbose", "--backtrack=100", "--keep-going", "@world"],
            ["/usr/bin/emerge", "--exclude=sys-devel/gcc", "@preserved-rebuild"],
            ["/usr/bin/emerge", "--exclude=sys-devel/gcc", "--depclean", "--ask=n"],
        ])
        lines = self.world.read_text().splitlines()
        self.assertNotIn("app-admin/ansible", lines)
        self.assertIn("# preserve", lines)
        for atom in REQUIRED:
            self.assertEqual(lines.count(atom), 1)
        self.assertFalse(any(line.startswith("/bin/") for line in lines))
        before = self.world.read_bytes()
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.world.read_bytes(), before)

    def test_pinned_rootfs_skips_all_writes_and_package_commands(self):
        self.declaration.write_text(json.dumps({
            "schema": "homecluster-pxe-source-declaration-v1", "environment": "staging",
            "build_id": "fixture-generation", "infra_revision": "a" * 40,
        }))
        before = self.world.read_bytes()
        status, output = self.invoke(self.service_args())
        self.assertEqual(status, 0)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.world.read_bytes(), before)
        self.assertIn("pinned", output.lower())

    def test_bad_source_declaration_fails_before_any_write(self):
        for body in ("", "{", "[]", '{"environment":"staging"}'):
            with self.subTest(body=body):
                self.declaration.write_text(body)
                before = self.world.read_bytes()
                self.assertNotEqual(self.invoke()[0], 0)
                self.assertEqual(self.commands, [])
                self.assertEqual(self.world.read_bytes(), before)

    def test_symlinked_source_declaration_fails_closed(self):
        target = self.root / "target"
        target.write_text("{}")
        self.declaration.symlink_to(target)
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(self.commands, [])

    def test_world_write_failure_prevents_sync(self):
        self.world.unlink()
        self.world.mkdir()
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(self.commands, [])

    def test_command_failure_stops_following_phases(self):
        for position in (1, 2, 3, 4, 5):
            with self.subTest(position=position):
                self.commands.clear()
                self.fail_at = position
                self.assertNotEqual(self.invoke()[0], 0)
                self.assertEqual(len(self.commands), position)


if __name__ == "__main__":
    unittest.main()
