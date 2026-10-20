#!/usr/bin/env python3
"""Synthetic fixed-path boot declaration reader checks."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "pxe_node_source_declaration_status.py"
spec = importlib.util.spec_from_file_location("pxe_node_source_declaration_tested", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

BOOT = "11111111-2222-3333-4444-555555555555"
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.boot = root / "boot_id"
        self.file = root / "source-declaration.json"
        self.boot.write_text(BOOT + "\n", encoding="ascii")
        self.value = {
            "schema": "homecluster-pxe-source-declaration-v1",
            "environment": "staging", "build_id": "fixed-generation-run",
            "infra_revision": "a" * 40,
        }
        self.write()

    def write(self):
        self.file.write_text(json.dumps(self.value) + "\n", encoding="utf-8")
        self.file.chmod(0o644)

    def observe(self):
        return mod.snapshot(boot_path=self.boot, declaration_path=self.file,
                            now=NOW, expected_uid=os.getuid())

    def test_fixed_declaration_is_partial_not_execution_attestation(self):
        value = self.observe()
        self.assertEqual(value["status"], "partial")
        self.assertEqual(value["boot_id"], BOOT)
        self.assertEqual(value["infra_revision"], "a" * 40)
        self.assertEqual(value["build_id"], "fixed-generation-run")
        self.assertEqual(value["observed_at"], "2026-10-20T12:00:00Z")
        self.assertIn("not_execution_attested", value["reason"])

    def test_invalid_identity_and_secret_extra_are_unknown_without_echo(self):
        for field, invalid in (("infra_revision", "A" * 40),
                               ("environment", "production"),
                               ("build_id", "bad;id")):
            with self.subTest(field=field):
                self.value[field] = invalid
                self.write()
                self.assertEqual(self.observe()["status"], "unknown")
                self.value[field] = {
                    "infra_revision": "a" * 40,
                    "environment": "staging", "build_id": "fixed-generation-run",
                }[field]
        self.value["token"] = "SECRET"
        self.write()
        self.assertNotIn("SECRET", json.dumps(self.observe()))

    def test_duplicate_keys_and_symlink_are_unknown(self):
        self.file.write_text('{"schema":"x","schema":"y"}\n', encoding="utf-8")
        self.assertEqual(self.observe()["status"], "unknown")
        self.file.unlink()
        target = Path(self.tmp.name) / "target"
        target.write_text(json.dumps(self.value) + "\n", encoding="utf-8")
        self.file.symlink_to(target)
        self.assertEqual(self.observe()["status"], "unknown")

    def test_unexpected_owner_is_unknown(self):
        self.assertEqual(mod.snapshot(boot_path=self.boot, declaration_path=self.file,
                                      now=NOW, expected_uid=os.getuid() + 1)["status"],
                         "unknown")

    def test_boot_change_and_invalid_time_are_unknown(self):
        class ChangingBoot:
            count = 0
            def read_text(self, **_):
                self.count += 1
                return BOOT if self.count == 1 else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.assertEqual(mod.snapshot(boot_path=ChangingBoot(),
                                      declaration_path=self.file, now=NOW,
                                      expected_uid=os.getuid())["reason"],
                         "boot_identity_changed")
        self.assertEqual(mod.snapshot(boot_path=self.boot,
                                      declaration_path=self.file,
                                      now=datetime(2026, 10, 20),
                                      expected_uid=os.getuid())["reason"],
                         "observation_time_invalid")


if __name__ == "__main__":
    unittest.main()
