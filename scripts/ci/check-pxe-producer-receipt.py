#!/usr/bin/env python3
"""Temporary paired artifact fixtures; no real rootfs, SSH or mounts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import yaml

FILES = Path(__file__).resolve().parents[2] / "ansible/openwrt/roles/openwrt_gentoo_rootfs/files"
sys.path.insert(0, str(FILES))
import pxe_producer_receipt as producer

DATE = "20261018"
ARGS = {"bundle_date": DATE, "build_id": "fixture-build", "infra_revision": "a" * 40,
        "generation_profile": "rpi5-common-kernel"}


class ProducerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "gentoo"
        (self.root / "releases/producer").mkdir(parents=True)
        patcher = mock.patch.object(producer, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = self.root / "releases/producer" / (DATE + ".json")
        self.manifest_path = self.root / "releases" / (DATE + ".json")
        self.manifest = {"schema": 1, "policy": "paired_clean", "bundle_date": DATE,
                         "releases": {name: f"{DATE}-{name}" for name in ("rpi4", "rpi5")},
                         "target_hosts": ["private-fixture-host"], "target_stages": ["stg"],
                         "required_tftp_paths": [], "markers": [], "metadata": {"private": "must-not-be-copied"}}
        self.declaration = {"schema": "homecluster-pxe-source-declaration-v1", "environment": "staging",
                            "build_id": ARGS["build_id"], "infra_revision": ARGS["infra_revision"]}
        for generation in ("rpi4", "rpi5"):
            declaration = self.root / f"{DATE}-{generation}/nfs/etc/ansible-pull/source-declaration.json"
            declaration.parent.mkdir(parents=True)
            declaration.write_text(json.dumps(self.declaration) + "\n")
            tftp = self.tftp(generation)
            (tftp / "overlays").mkdir(parents=True)
            dtb = "bcm2711-rpi-4-b.dtb" if generation == "rpi4" else "bcm2712-rpi-5-b.dtb"
            for name in ("kernel8.img", "initramfs-pxe.img", dtb, "overlays"):
                if name != "overlays":
                    (tftp / name).write_bytes((generation + ":" + name).encode())
                self.manifest["required_tftp_paths"].append({"generation": generation,
                                                          "release": f"{DATE}-{generation}", "path": name})
        self.write_manifest()

    def tftp(self, generation="rpi4"):
        return self.root / "tftp-root/dates" / f"{DATE}-{generation}"

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest) + "\n")

    def run_producer(self, **overrides):
        return producer.produce(**{**ARGS, **overrides})

    def assert_rejected(self, **overrides):
        with self.assertRaises((ValueError, OSError)):
            self.run_producer(**overrides)
        self.assertFalse(self.output.exists())

    def test_complete_pair_binds_actual_manifest_declarations_and_trees(self):
        result = self.run_producer()
        raw = self.output.read_bytes()
        value = json.loads(raw)
        payload = {key: item for key, item in value.items() if key != "receipt_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        expected_id = "sha256:" + hashlib.sha256(b"homecluster-pxe-producer-receipt-v1\x00" + canonical).hexdigest()
        self.assertEqual(value["receipt_id"], expected_id)
        self.assertEqual(result["receipt_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(value["artifact_manifest_sha256"], hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(set(value["artifacts"]), {"rpi4", "rpi5"})
        self.assertEqual(result["status"], "written")
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o444)
        self.assertNotIn(b"private-fixture-host", raw)
        self.assertNotIn(b"must-not-be-copied", raw)
        self.assertNotIn(b"seal", raw)

    def test_identical_artifact_reuses_receipt_without_rewriting_capture_time(self):
        with mock.patch.object(producer, "_now", return_value="2026-10-18T00:00:00Z"):
            first = self.run_producer()
        info, raw = self.output.stat(), self.output.read_bytes()
        with mock.patch.object(producer, "_now", return_value="2026-10-18T01:00:00Z"):
            second = self.run_producer()
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(raw, self.output.read_bytes())
        self.assertEqual(info.st_ino, self.output.stat().st_ino)
        self.assertEqual(info.st_mtime_ns, self.output.stat().st_mtime_ns)

    def test_changed_artifact_cannot_overwrite_old_receipt(self):
        self.run_producer()
        raw = self.output.read_bytes()
        (self.tftp() / "kernel8.img").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.run_producer()
        self.assertEqual(raw, self.output.read_bytes())

    def test_bad_inputs_cannot_choose_other_paths(self):
        for key, value in (("bundle_date", "../escape"), ("bundle_date", "20260230"),
                           ("build_id", "a\n"), ("infra_revision", "A" * 40),
                           ("generation_profile", "prod"), ("build_id", True)):
            with self.subTest(key=key, value=value):
                self.assert_rejected(**{key: value})

    def test_missing_or_conflicting_declaration_rejected(self):
        path = self.root / f"{DATE}-rpi5/nfs/etc/ansible-pull/source-declaration.json"
        for field, value in (("environment", "prod"), ("build_id", "other"), ("infra_revision", "b" * 40)):
            path.write_text(json.dumps({**self.declaration, field: value}))
            self.assert_rejected()
        path.unlink()
        self.assert_rejected()

    def test_declaration_bytes_must_match_between_generations(self):
        path = self.root / f"{DATE}-rpi5/nfs/etc/ansible-pull/source-declaration.json"
        path.write_text(json.dumps(self.declaration, indent=2))
        self.assert_rejected()

    def test_incomplete_manifest_and_other_environment_rejected(self):
        original = deepcopy(self.manifest)
        for field, value in (("target_stages", ["prod"]), ("releases", {"rpi4": DATE + "-rpi4"}),
                             ("required_tftp_paths", []), ("schema", True)):
            self.manifest = {**original, field: value}
            self.write_manifest()
            self.assert_rejected()

    def test_required_path_traversal_duplicate_and_missing_boot_kind_rejected(self):
        original = deepcopy(self.manifest)
        for path in ("../outside", "/absolute", "overlays/../x", "a//b"):
            self.manifest = deepcopy(original)
            self.manifest["required_tftp_paths"][0]["path"] = path
            self.write_manifest()
            self.assert_rejected()
        self.manifest = deepcopy(original)
        self.manifest["required_tftp_paths"].append(self.manifest["required_tftp_paths"][0])
        self.write_manifest()
        self.assert_rejected()
        self.manifest = deepcopy(original)
        self.manifest["required_tftp_paths"] = [item for item in original["required_tftp_paths"] if not item["path"].endswith(".dtb")]
        self.write_manifest()
        self.assert_rejected()

    def test_required_boot_file_cannot_be_symlink_empty_or_missing(self):
        path = self.tftp() / "kernel8.img"
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        path.unlink()
        path.symlink_to(outside)
        self.assert_rejected()
        path.unlink()
        path.write_bytes(b"")
        self.assert_rejected()
        path.unlink()
        self.assert_rejected()

    def test_measurement_error_does_not_publish_partial_receipt(self):
        with mock.patch.object(producer, "fingerprint_tree", side_effect=ValueError("unmounted evidence unavailable")):
            self.assert_rejected()

    def test_manifest_change_during_measurement_rejected(self):
        actual = producer.fingerprint_tree
        def changing_measurement(root):
            result = actual(root)
            self.manifest["metadata"]["changed"] = True
            self.write_manifest()
            return result
        with mock.patch.object(producer, "fingerprint_tree", side_effect=changing_measurement):
            self.assert_rejected()

    def test_clock_reversal_rejected(self):
        with mock.patch.object(producer, "_now", side_effect=["2026-10-18T01:00:00Z", "2026-10-18T00:00:00Z"]):
            self.assert_rejected()

    def test_atomic_publication_failure_has_no_receipt_or_temporary_residue(self):
        with mock.patch.object(producer.os, "link", side_effect=OSError("fixture")):
            self.assert_rejected()
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_duplicate_json_keys_and_existing_semantic_tampering_rejected(self):
        raw = self.manifest_path.read_bytes()
        self.manifest_path.write_bytes(raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1'))
        self.assert_rejected()
        self.manifest_path.write_bytes(raw)
        self.run_producer()
        value = json.loads(self.output.read_bytes())
        value["artifacts"]["rpi4"]["rootfs"]["entries"] = True
        value["receipt_id"] = producer._receipt_id(value)
        self.output.chmod(0o600)
        self.output.write_bytes(producer._canonical(value) + b"\n")
        raw = self.output.read_bytes()
        with self.assertRaises(ValueError):
            self.run_producer()
        self.assertEqual(self.output.read_bytes(), raw)

    def invoke_role(self, overrides=None):
        role = FILES.parent
        workspace = self.root.parent / "ansible-fixture"
        copied = workspace / "roles/openwrt_gentoo_rootfs"
        for directory in ("tasks", "files", "defaults"):
            (copied / directory).mkdir(parents=True, exist_ok=True)
        installed = self.root.parent / "installed-producer"
        tasks = yaml.safe_load((role / "tasks/producer_receipt.yml").read_text())

        def transform(value):
            if isinstance(value, dict):
                return {key: transform(item) for key, item in value.items() if key not in ("owner", "group")}
            if isinstance(value, list):
                return [transform(item) for item in value]
            if isinstance(value, str):
                return value.replace("/srv/gentoo", str(self.root)).replace(
                    "/usr/local/libexec/homecluster-pxe-producer", str(installed))
            return value

        (copied / "tasks/producer_receipt.yml").write_text(yaml.safe_dump(transform(tasks)))
        shutil.copy(role / "defaults/main.yml", copied / "defaults/main.yml")
        shutil.copy(FILES / "pxe_artifact_fingerprint.py", copied / "files/pxe_artifact_fingerprint.py")
        source = (FILES / "pxe_producer_receipt.py").read_text()
        self.assertEqual(source.count('ROOT = Path("/srv/gentoo")'), 1)
        (copied / "files/pxe_producer_receipt.py").write_text(source.replace(
            'ROOT = Path("/srv/gentoo")', "ROOT = Path(" + repr(str(self.root)) + ")"))
        values = {
            "openwrt_gentoo_release_producer_enabled": True,
            "openwrt_gentoo_release_producer_generation_profile": ARGS["generation_profile"],
            "openwrt_gentoo_ansible_pull_source_mode": "pinned",
            "openwrt_gentoo_ansible_pull_source_environment": "staging",
            "openwrt_gentoo_ansible_pull_source_revision": ARGS["infra_revision"],
            "openwrt_gentoo_ansible_pull_source_build_id": ARGS["build_id"],
            "openwrt_gentoo_release_bundle_manifest_dir": str(self.root / "releases"),
            "pxe_release_bundle_effective_date": DATE, "pxe_release_bundle_stage": "stg",
            **(overrides or {}),
        }
        play = [{"hosts": "localhost", "gather_facts": False, "become": False, "vars": values,
                 "tasks": [{"ansible.builtin.include_role": {"name": "openwrt_gentoo_rootfs",
                                                             "tasks_from": "producer_receipt"}}]}]
        (workspace / "test.yml").write_text(yaml.safe_dump(play))
        (workspace / "ansible.cfg").write_text("[defaults]\nretry_files_enabled=False\n")
        env = {"PATH": os.environ["PATH"], "HOME": str(workspace), "LANG": "C.UTF-8",
               "ANSIBLE_CONFIG": str(workspace / "ansible.cfg"), "ANSIBLE_NOCOLOR": "1",
               "ANSIBLE_LOCAL_TEMP": str(workspace / "local-tmp"),
               "ANSIBLE_REMOTE_TEMP": str(workspace / "remote-tmp"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", "test.yml"],
                              cwd=workspace, env=env, text=True, capture_output=True, timeout=90), installed

    def test_ansible_role_really_publishes_from_completed_temporary_artifacts(self):
        result, installed = self.invoke_role()
        self.assertEqual(result.returncode, 0, result.stdout[-2200:] + result.stderr[-1000:])
        self.assertTrue((installed / "pxe_producer_receipt.py").is_file())
        self.assertEqual(json.loads(self.output.read_bytes())["infra_generation_revision"], ARGS["infra_revision"])

    def test_ansible_rejects_bad_scope_before_install_or_publication(self):
        cases = ({"pxe_release_bundle_stage": "prod"},
                 {"openwrt_gentoo_ansible_pull_source_mode": "legacy"},
                 {"openwrt_gentoo_ansible_pull_source_revision": "a" * 40 + "\n"},
                 {"openwrt_gentoo_release_producer_generation_profile": "unknown"},
                 {"openwrt_gentoo_release_bundle_manifest_dir": "/tmp/other"})
        for changes in cases:
            with self.subTest(changes=changes):
                result, installed = self.invoke_role(changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(installed.exists())
                self.assertFalse(self.output.exists())

    def test_disabled_legacy_role_has_no_install_or_publication(self):
        result, installed = self.invoke_role({"openwrt_gentoo_release_producer_enabled": False,
                                             "openwrt_gentoo_ansible_pull_source_mode": "legacy"})
        self.assertEqual(result.returncode, 0, result.stdout[-1500:])
        self.assertFalse(installed.exists())
        self.assertFalse(self.output.exists())

    def test_standard_profile_can_publish(self):
        self.run_producer(generation_profile="standard")
        self.assertEqual(json.loads(self.output.read_bytes())["generation_profile"], "standard")

    def test_hooks_follow_last_manifest_writer_and_are_opt_in(self):
        playbooks = FILES.parents[2] / "playbooks"
        for name in ("pxe-release-bundle-build.yml", "pxe-release-bundle-staging.yml"):
            tasks = yaml.safe_load((playbooks / name).read_text())[0]["tasks"]
            hook = tasks[-1]
            self.assertEqual(hook["ansible.builtin.include_role"]["tasks_from"], "producer_receipt")
            self.assertIn("default(false)", hook["when"])
        common = yaml.safe_load((playbooks / "pxe-release-bundle-staging-with-common-kernel.yml").read_text())
        self.assertEqual(common[-1]["ansible.builtin.import_playbook"], "pxe-release-bundle-build.yml")
        defaults = yaml.safe_load((FILES.parent / "defaults/main.yml").read_text())
        self.assertIs(defaults["openwrt_gentoo_release_producer_enabled"], False)
        tasks = yaml.safe_load((FILES.parent / "tasks/producer_receipt.yml").read_text())[0]["block"]
        self.assertEqual(tasks[1]["ansible.builtin.file"]["owner"], "root")
        self.assertEqual(tasks[2]["ansible.builtin.copy"]["group"], "root")


if __name__ == "__main__":
    unittest.main()
