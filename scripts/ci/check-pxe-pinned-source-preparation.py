#!/usr/bin/env python3
"""Check pinned source-preparation cache against a real local Git checkout."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-ansible-pull-wrapper.sh.j2"
FUNCTIONS = WRAPPER.read_text().split("pinned_prepare_marker() {", 1)[1].split(
    "# Optional evidence of checkout HEAD", 1
)[0]
FUNCTIONS = "pinned_prepare_marker() {" + FUNCTIONS


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class PinnedPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.checkout = self.root / "checkout"
        self.arm64 = self.checkout / "ansible/arm64"
        self.arm64.mkdir(parents=True)
        self.requirements = self.arm64 / "requirements.yml"
        self.requirements.write_text(
            'roles:\n  - name: xanmanning.k3s\n    version: "v3.6.2"\n'
            'collections:\n  - name: community.general\n    version: "13.5.0"\n'
        )
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        git(self.checkout, "add", "ansible/arm64/requirements.yml")
        subprocess.run(
            ["git", "-C", str(self.checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
            check=True,
        )
        role_meta = self.arm64 / "roles/xanmanning.k3s/meta"
        role_meta.mkdir(parents=True)
        (role_meta / ".galaxy_install_info").write_text("version: v3.6.2\n")
        self.collection_manifest = self.root / "home/.ansible/collections/ansible_collections/community/general/MANIFEST.json"
        self.collection_manifest.parent.mkdir(parents=True)
        self.collection_manifest.write_text(json.dumps({"collection_info": {"version": "13.5.0"}}))
        (self.root / "state").mkdir()
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.root / "home"),
            DEST=str(self.checkout),
            ARM64_PLAYBOOK_DIR=str(self.arm64),
            STATE_DIR=str(self.root / "state"),
            pin_revision=git(self.checkout, "rev-parse", "HEAD"),
            pin_build_id="fixture-build",
            pin_digest="d" * 64,
            prepare_boot_id="fixture-boot",
            requirements_sha=hashlib.sha256(self.requirements.read_bytes()).hexdigest(),
        )

    def check(self, statement="pinned_prepare_is_current") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", "-c", FUNCTIONS + "\n" + statement],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=5,
        )

    def test_reuses_only_complete_clean_preparation(self):
        self.assertEqual(self.check("record_pinned_prepare").returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        (self.checkout / "unexpected.yml").write_text("unsafe\n")
        self.assertNotEqual(self.check().returncode, 0)

    def test_missing_role_or_collection_invalidates_cache(self):
        self.assertEqual(self.check("record_pinned_prepare").returncode, 0)
        (self.arm64 / "roles/xanmanning.k3s/meta/.galaxy_install_info").unlink()
        self.assertNotEqual(self.check().returncode, 0)
        (self.arm64 / "roles/xanmanning.k3s/meta/.galaxy_install_info").write_text("version: v3.6.2\n")
        self.collection_manifest.unlink()
        self.assertNotEqual(self.check().returncode, 0)

    def test_version_or_pin_change_invalidates_cache(self):
        self.assertEqual(self.check("record_pinned_prepare").returncode, 0)
        self.env["pin_revision"] = "a" * 40
        self.assertNotEqual(self.check().returncode, 0)
        self.env["pin_revision"] = git(self.checkout, "rev-parse", "HEAD")
        self.collection_manifest.write_text(json.dumps({"collection_info": {"version": "0.0.0"}}))
        self.assertNotEqual(self.check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
