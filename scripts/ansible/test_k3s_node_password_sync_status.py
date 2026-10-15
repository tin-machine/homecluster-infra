#!/usr/bin/env python3
"""Exercise the PXE password hook with synthetic files outside the live paths."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "ansible/arm64/roles/k3s_local_storage/templates/k3s-node-password-sync.sh.j2"


def render(directory: Path) -> Path:
    mount = directory / "data"
    state = mount / ".state"
    local = directory / "rootfs/etc/rancher/node"
    status = directory / "run/k3s-node-password-sync"
    replacements = {
        "{{ k3s_local_storage_mountpoint }}": str(mount),
        "{{ k3s_local_storage_state_dir }}": str(state),
        "{{ k3s_local_storage_node_password_state_path }}": str(state / "node-password"),
        "{{ k3s_local_storage_server_ca_hash_state_path }}": str(state / "server-ca-hash"),
        "{{ k3s_local_storage_cluster_token_path }}": str(directory / "cluster-token"),
        "{{ k3s_local_storage_reset_identity_when_local_password_missing | bool | ternary('true', 'false') }}": "false",
        'LOCAL_DIR="/etc/rancher/node"': f'LOCAL_DIR="{local}"',
        'STATUS_DIR="/run/k3s-node-password-sync"': f'STATUS_DIR="{status}"',
    }
    source = TEMPLATE.read_text(encoding="utf-8")
    for old, new in replacements.items():
        if old not in source:
            raise AssertionError(f"template fragment missing: {old}")
        source = source.replace(old, new)
    if "{{" in source or "{%" in source:
        raise AssertionError("unrendered template")
    script = directory / "k3s-node-password-sync.sh"
    script.write_text(source, encoding="utf-8")
    script.chmod(0o700)
    return script


def fixture_env(directory: Path, *, mounted: bool = True) -> dict[str, str]:
    binary = directory / "bin"
    binary.mkdir(exist_ok=True)
    findmnt = binary / "findmnt"
    findmnt.write_text(
        '#!/bin/sh\nif [ "${MOUNT_AVAILABLE:-1}" = 1 ]; then printf "%s\\n" "$3"; else printf "/\\n"; fi\n',
        encoding="utf-8",
    )
    findmnt.chmod(0o700)
    sleep = binary / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o700)
    return {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "MOUNT_AVAILABLE": "1" if mounted else "0"}


def read_status(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


class BootStatusTests(unittest.TestCase):
    def test_restore_persist_and_first_boot_evidence_are_secret_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k3s-password-status-") as name:
            root = Path(name)
            state = root / "data/.state"
            state.mkdir(parents=True)
            local = root / "rootfs/etc/rancher/node/password"
            status = root / "run/k3s-node-password-sync"
            password = "synthetic-password-must-not-appear"
            token = "K10" + "a" * 64 + "::synthetic-token-must-not-appear"
            (state / "node-password").write_text(password + "\n", encoding="utf-8")
            (root / "cluster-token").write_text(token + "\n", encoding="utf-8")
            script = render(root)

            environment = fixture_env(root)
            first = subprocess.run([str(script), "restore"], env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(local.read_text(encoding="utf-8"), password + "\n")
            restored = read_status(status / "restore-first.state")
            self.assertEqual(restored["outcome"], "restored_ssd")
            self.assertEqual(restored["local_password_present_before"], "false")
            self.assertEqual(restored["ssd_password_present_before"], "true")
            self.assertRegex(restored["boot_id"], r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
            self.assertEqual(os.stat(status).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(status / "restore-first.state").st_mode & 0o777, 0o600)

            persisted = subprocess.run([str(script), "persist"], env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(persisted.returncode, 0, persisted.stderr)
            self.assertEqual(read_status(status / "persist.state")["outcome"], "persisted")
            again = subprocess.run([str(script), "restore"], env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(read_status(status / "restore.state")["local_password_present_before"], "true")
            self.assertEqual(read_status(status / "restore-first.state"), restored)
            evidence = "\n".join(path.read_text(encoding="utf-8") for path in status.iterdir())
            self.assertNotIn(password, evidence)
            self.assertNotIn(token, evidence)
            self.assertNotIn("synthetic-token", first.stdout + first.stderr + persisted.stdout + persisted.stderr)

    def test_parent_filesystem_is_not_accepted_as_ssd_mount(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k3s-password-mount-") as name:
            root = Path(name)
            state = root / "data/.state"
            state.mkdir(parents=True)
            (state / "node-password").write_text("synthetic-password\n", encoding="utf-8")
            script = render(root)
            result = subprocess.run(
                [str(script), "restore"], env=fixture_env(root, mounted=False),
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root / "rootfs/etc/rancher/node/password").exists())
            restored = read_status(root / "run/k3s-node-password-sync/restore-first.state")
            self.assertEqual(restored["outcome"], "mount_unavailable")
            self.assertEqual(restored["ssd_password_present_before"], "unknown")


if __name__ == "__main__":
    unittest.main()
