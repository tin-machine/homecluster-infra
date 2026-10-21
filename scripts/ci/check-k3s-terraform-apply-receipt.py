#!/usr/bin/env python3
"""Execute the actual completion block with synthetic non-secret bundle markers."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


TEMPLATE = (Path(__file__).resolve().parents[2] / "ansible/arm64/roles"
            / "k3s_observability_apply/templates/k3s-observability-apply.sh.j2")
START = "# The done marker is also a boot-scoped, source-labelled success receipt."
END = 'echo "k3s observability apply completed for bundle ${INFRA_BUNDLE_ID}"'
PRE_START = "for marker in .homecluster-bundle-id .homecluster-infra-revision; do"
PRE_END = "read_contract_version() {"


def completion_block() -> str:
    source = TEMPLATE.read_text(encoding="utf-8")
    start = source.index(START)
    end = source.index(END, start)
    return "set -eu\n" + source[start:end]


def preapply_block() -> str:
    source = TEMPLATE.read_text(encoding="utf-8")
    start = source.index(PRE_START)
    end = source.index(PRE_END, start)
    return "set -eu\n" + source[start:end]


class ApplyReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "staging.done"
        self.infra = Path(self.tmp.name) / "infra"
        self.site = Path(self.tmp.name) / "site"
        self.infra.mkdir()
        self.site.mkdir()
        bundle = "a" * 12 + "-" + "b" * 12
        for root in (self.infra, self.site):
            (root / ".homecluster-bundle-id").write_text(bundle + "\n", encoding="ascii")
            (root / ".homecluster-infra-revision").write_text("a" * 40 + "\n", encoding="ascii")
        (self.site / ".homecluster-inventory-revision").write_text("b" * 40 + "\n", encoding="ascii")
        self.env = {
            **os.environ,
            "DONE_MARKER": str(self.path),
            "INFRA_SOURCE_DIR": str(self.infra),
            "SITE_INPUT_DIR": str(self.site),
            "INFRA_BUNDLE_ID": bundle,
            "INFRA_REVISION": "a" * 40,
            "SITE_INPUT_INVENTORY_REVISION": "b" * 40,
        }

    def run_block(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", "-c", completion_block()], env=self.env,
                              capture_output=True, text=True, check=False)

    def run_preapply(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", "-c", preapply_block()], env=self.env,
                              capture_output=True, text=True, check=False)

    def test_preapply_requires_full_inventory_revision_and_matched_bundle(self) -> None:
        self.assertEqual(self.run_preapply().returncode, 0)
        marker = self.site / ".homecluster-inventory-revision"
        marker.unlink()
        self.assertNotEqual(self.run_preapply().returncode, 0)
        marker.write_text("c" * 40 + "\n", encoding="ascii")
        self.assertNotEqual(self.run_preapply().returncode, 0)

    def test_success_receipt_is_private_and_source_labelled(self) -> None:
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        lines = self.path.read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines[0], "SCHEMA=homecluster-terraform-apply-receipt-v1")
        self.assertEqual(lines[1], "SCOPE=terraform_apply_success_not_helm_state_attested")
        self.assertRegex(lines[2], r"^BOOT_ID=[0-9a-f-]{36}$")
        self.assertEqual(lines[3], "BUNDLE_ID=" + self.env["INFRA_BUNDLE_ID"])
        self.assertEqual(lines[4], "INFRA_REVISION=" + self.env["INFRA_REVISION"])
        self.assertEqual(lines[5], "INVENTORY_REVISION=" + self.env["SITE_INPUT_INVENTORY_REVISION"])
        self.assertRegex(lines[6], r"^APPLIED_AT_EPOCH=[0-9]+$")

    def test_invalid_source_identity_never_marks_completion(self) -> None:
        for key, value in (("INFRA_BUNDLE_ID", "invalid"),
                           ("INFRA_REVISION", "A" * 40)):
            with self.subTest(key=key):
                self.env[key] = value
                self.assertNotEqual(self.run_block().returncode, 0)
                self.assertFalse(self.path.exists())
                self.env[key] = ("a" * 12 + "-" + "b" * 12
                                 if key == "INFRA_BUNDLE_ID" else "a" * 40)

    def test_changed_staged_marker_never_marks_completion(self) -> None:
        (self.site / ".homecluster-inventory-revision").write_text("c" * 40 + "\n", encoding="ascii")
        self.assertNotEqual(self.run_block().returncode, 0)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
