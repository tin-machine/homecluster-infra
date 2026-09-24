#!/usr/bin/env python3
"""Validate the shared PXE lower-rootfs hostname source and runtime contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BUILD_TASKS = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"
CLONE_TASKS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/rootfs_clone.yml"


class ContractError(RuntimeError):
    pass


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise ContractError(f"missing {label}: {needle}")


def validate_runtime_rootfs(rootfs: Path) -> None:
    hostname = rootfs / "etc/hostname"
    if hostname.exists() or hostname.is_symlink():
        raise ContractError(f"shared lower hostname must be absent: {hostname}")


def validate_source_contract(build_tasks: str, clone_tasks: str) -> None:
    require(build_tasks, "PXE shared lower rootfsからhost固有hostnameを削除", "build cleanup task")
    require(build_tasks, 'path: "/srv/gentoo/{{ item.release }}/nfs/etc/hostname"', "release-scoped hostname path")
    require(build_tasks, "state: absent", "build hostname removal")
    require(build_tasks, "pxe_release_bundle_shared_lower_hostname_stats", "post-build hostname stat")
    require(build_tasks, "not (item.stat.exists | default(false))", "post-build absence gate")
    require(clone_tasks, 'rm -f "${tmp}/etc/hostname"', "clone hostname cleanup")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        rootfs = Path(temporary)
        (rootfs / "etc").mkdir()
        validate_runtime_rootfs(rootfs)

        (rootfs / "etc/hostname").write_text("node-a\n", encoding="utf-8")
        try:
            validate_runtime_rootfs(rootfs)
        except ContractError:
            pass
        else:
            raise AssertionError("host-specific hostname fixture was accepted")

        (rootfs / "etc/hostname").write_text("localhost\n", encoding="utf-8")
        try:
            validate_runtime_rootfs(rootfs)
        except ContractError:
            pass
        else:
            raise AssertionError("localhost hostname fixture was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    validate_source_contract(
        BUILD_TASKS.read_text(encoding="utf-8"),
        CLONE_TASKS.read_text(encoding="utf-8"),
    )
    if args.self_test:
        self_test()
    if args.rootfs is not None:
        validate_runtime_rootfs(args.rootfs)
    print("PXE shared lower hostname contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError) as error:
        print(f"PXE shared lower hostname contract failed: {error}")
        raise SystemExit(1)
