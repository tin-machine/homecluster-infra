#!/usr/bin/env python3
"""Exercise bundle role-variable isolation with synthetic localhost releases."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"
INITRAMFS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/initramfs.yml"


def fixture(*, include_override: bool) -> subprocess.CompletedProcess[str]:
    build = yaml.safe_load(BUNDLE.read_text())[0]
    # Execute the production normalizer, but never its stat, chroot or copy tasks.
    normalizer = yaml.safe_load(INITRAMFS.read_text())[0]["block"][:2]
    role_vars = {}
    if include_override and "openwrt_gentoo_initramfs_builds" in build["vars"]:
        role_vars["openwrt_gentoo_initramfs_builds"] = build["vars"]["openwrt_gentoo_initramfs_builds"]
    assertions = [
        "openwrt_gentoo_initramfs_rootfs_dirs == ['/srv/gentoo/' ~ pxe_release_bundle_build_item.release ~ '/nfs']",
        "openwrt_gentoo_initramfs_tftp_dirs == ['/srv/gentoo/tftp-root/dates/' ~ pxe_release_bundle_build_item.release]",
        "openwrt_gentoo_initramfs_builds_effective | length == (2 if pxe_release_bundle_build_item.name == 'rpi5' else 1)",
        "openwrt_gentoo_initramfs_builds_effective[0].kernel_version_glob == '*-v8+'",
        "openwrt_gentoo_initramfs_builds_effective[0].output_path == '/boot/initramfs-pxe-v8.img'",
        "openwrt_gentoo_initramfs_builds_effective[0].tftp_path == 'initramfs-pxe-v8.img'",
        "pxe_release_bundle_build_item.name != 'rpi5' or openwrt_gentoo_initramfs_builds_effective[1].kernel_version_glob == '*-v8-16k+'",
        "pxe_release_bundle_build_item.name != 'rpi5' or openwrt_gentoo_initramfs_builds_effective[1].tftp_path == 'initramfs-pxe-v8-16k.img'",
    ]
    capture_tasks = normalizer + [{"name": "Assert current release isolation",
        "ansible.builtin.assert": {"that": assertions, "fail_msg": "bundle_initramfs_scope_invalid"}}]
    play = [{
        "hosts": "all", "gather_facts": False,
        "vars": {
            "openwrt_gentoo_rootfs_dir": "/srv/gentoo/20981201-rpi4/nfs",
            "openwrt_gentoo_tftp_dir": "/srv/gentoo/tftp-root/dates/20981201-rpi4",
        },
        "tasks": [{
            "ansible.builtin.include_role": {"name": "capture"},
            "loop": [{"name": "rpi4", "release": "20990101-rpi4"},
                     {"name": "rpi5", "release": "20990101-rpi5"}],
            "loop_control": {"loop_var": "pxe_release_bundle_build_item"},
            "vars": role_vars,
        }],
    }]
    # Host vars reproduce the old catalog leaking into an include_role build.
    inventory = {"all": {"hosts": {"localhost": {
        "ansible_connection": "local",
        "openwrt_gentoo_initramfs_builds": [{
            "name": "historical", "rootfs_release": "20980101-rpi5",
            "kernel_version_glob": "*-v8-nvidia+", "output_path": "/boot/old.img",
            "tftp_path": "old.img",
        }],
    }}}}
    with tempfile.TemporaryDirectory(prefix="pxe-initramfs-scope-") as temporary:
        root = Path(temporary)
        (root / "roles/capture/tasks").mkdir(parents=True)
        (root / "roles/capture/tasks/main.yml").write_text(json.dumps(capture_tasks))
        (root / "playbook.yml").write_text(json.dumps(play))
        (root / "inventory.yml").write_text(json.dumps(inventory))
        (root / "ansible.cfg").write_text("[defaults]\nretry_files_enabled=False\n")
        return subprocess.run(
            ["ansible-playbook", "-i", str(root / "inventory.yml"), str(root / "playbook.yml")],
            cwd=root, env={**os.environ, "ANSIBLE_CONFIG": str(root / "ansible.cfg")},
            capture_output=True, text=True, timeout=60, check=False,
        )


def main() -> int:
    failures = []
    for name, override, expected in (("current_release", True, True), ("legacy_leak_rejected", False, False)):
        result = fixture(include_override=override)
        if (result.returncode == 0) != expected or (not expected and "bundle_initramfs_scope_invalid" not in result.stdout):
            failures.append({"case": name, "exit_code": result.returncode,
                             "stdout_tail": result.stdout[-1800:], "stderr_tail": result.stderr[-500:]})
    print(json.dumps({"ok": not failures, "cases": 2, "failures": failures}, ensure_ascii=False))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
