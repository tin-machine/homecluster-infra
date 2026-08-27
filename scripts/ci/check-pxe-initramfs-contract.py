#!/usr/bin/env python3
"""Validate the deterministic Pi5 PXE initramfs artifact contract."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
INITRAMFS_TASKS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/initramfs.yml"
BUNDLE_TASKS = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"
COMMON_KERNEL_GATE = ROOT / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def require_ordered(text: str, needles: tuple[str, ...], label: str) -> None:
    positions = [text.find(needle) for needle in needles]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError(f"invalid {label}: {' -> '.join(needles)}")


def validate_contract(initramfs_tasks: str, bundle_tasks: str, common_kernel_gate: str) -> None:
    for needle, label in (
        ("- name: initramfs boot artifact contract を検証", "runtime semantic gate"),
        ('test -d "/lib/modules/$KVER"', "exact rootfs module ABI"),
        ('test -f "$image"', "initramfs regular file gate"),
        ('grep -F "lib/modules/$KVER/"', "exact initramfs module ABI"),
        ('/overlay\\\\.ko(\\\\.(gz|xz|zst))?', "overlay kernel module gate"),
        ('grep -F "mount-overlayfs.sh" >/dev/null', "overlay mount hook gate"),
        ('grep -F "overlayfs" >/dev/null', "overlay dracut module gate"),
        ('grep -F "nfs" >/dev/null', "NFS dracut module gate"),
        ('loop: "{{ openwrt_gentoo_initramfs_builds_effective | default([]) }}"', "all-build validation loop"),
    ):
        require(initramfs_tasks, needle, label)
    require_ordered(
        initramfs_tasks,
        (
            "- name: dracut で initramfs を生成",
            "- name: initramfs boot artifact contract を検証",
            "- name: TFTP へ initramfs を配置",
        ),
        "generate-validate-copy order",
    )

    require(bundle_tasks, "openwrt_gentoo_initramfs_force_build: true", "forced initramfs rebuild")
    for needle, label in (
        ("- name: rpi5-v8\n", "generic Pi5 initramfs build"),
        ("output_path: /boot/initramfs-pxe-v8.img", "generic Pi5 initramfs output"),
        ("tftp_path: initramfs-pxe-v8.img", "generic Pi5 TFTP artifact"),
        ("- name: rpi5-v8-nvidia\n", "NVIDIA Pi5 initramfs build"),
        ("output_path: /boot/initramfs-pxe-v8-nvidia.img", "NVIDIA Pi5 initramfs output"),
        ("tftp_path: initramfs-pxe-v8-nvidia.img", "NVIDIA Pi5 TFTP artifact"),
    ):
        require(bundle_tasks, needle, label)
    if bundle_tasks.count("kernel_version: \"{{ openwrt_rpi5_egpu_runtime_repair_kernel_version }}\"") < 2:
        raise AssertionError("generic and NVIDIA initramfs must use the exact common-kernel release")

    for needle, label in (
        ("for image in initramfs-pxe-v8.img initramfs-pxe-v8-nvidia.img", "both Pi5 gate artifacts"),
        ('cmp -s "$rootfs/boot/$image" "$tftp/$image"', "rootfs/TFTP byte identity"),
        ('grep -F "lib/modules/$kernel_release/"', "accepted exact ABI gate"),
        ("grep -F 'mount-overlayfs.sh' >/dev/null", "accepted overlay hook gate"),
    ):
        require(common_kernel_gate, needle, label)


def expect_failure(initramfs_tasks: str, bundle_tasks: str, common_kernel_gate: str, label: str) -> None:
    try:
        validate_contract(initramfs_tasks, bundle_tasks, common_kernel_gate)
    except AssertionError:
        return
    raise AssertionError(f"negative fixture unexpectedly passed: {label}")


def main() -> int:
    initramfs_tasks = INITRAMFS_TASKS.read_text(encoding="utf-8")
    bundle_tasks = BUNDLE_TASKS.read_text(encoding="utf-8")
    common_kernel_gate = COMMON_KERNEL_GATE.read_text(encoding="utf-8")
    validate_contract(initramfs_tasks, bundle_tasks, common_kernel_gate)

    expect_failure(
        initramfs_tasks.replace('grep -F "lib/modules/$KVER/"', "grep -F missing-kernel-abi", 1),
        bundle_tasks,
        common_kernel_gate,
        "missing exact ABI check",
    )
    expect_failure(
        initramfs_tasks,
        bundle_tasks.replace("- name: rpi5-v8\n", "- name: missing-generic\n", 1),
        common_kernel_gate,
        "missing generic rebuild",
    )
    expect_failure(
        initramfs_tasks.replace(
            "- name: initramfs boot artifact contract を検証",
            "- name: zzz initramfs boot artifact contract を検証",
            1,
        ),
        bundle_tasks,
        common_kernel_gate,
        "missing semantic gate",
    )
    expect_failure(
        initramfs_tasks,
        bundle_tasks,
        common_kernel_gate.replace('cmp -s "$rootfs/boot/$image" "$tftp/$image"', "true", 1),
        "missing rootfs/TFTP identity gate",
    )

    print("Pi5 PXE initramfs contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - emit one compact CI failure.
        print(f"Pi5 PXE initramfs contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
