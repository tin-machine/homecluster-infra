#!/usr/bin/env python3
"""Validate the deterministic Pi5 PXE initramfs artifact contract."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
INITRAMFS_TASKS = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/initramfs.yml"
BUNDLE_TASKS = ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"
COMMON_KERNEL_GATE = ROOT / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"

RUNTIME_VALIDATOR = r'''\
set -euo pipefail
KVER=6.18.36-v8-homecluster+
listing="$(cat)"
kernel_module_listing="$(grep -F "lib/modules/$KVER/" <<<"$listing")"
grep -Eq "/overlay\.ko(\.(gz|xz|zst))?([[:space:]]|$)" <<<"$kernel_module_listing"
grep -Fq "mount-overlayfs.sh" <<<"$listing"
grep -Fq "overlayfs" <<<"$listing"
grep -Fq "nfs" <<<"$listing"
'''

PIPELINE_RUNTIME_VALIDATOR = r'''\
set -o pipefail
KVER=6.18.36-v8-homecluster+
listing="$(cat)"
printf "%s\n" "$listing" | grep -F "lib/modules/$KVER/" | grep -Eq "/overlay\.ko(\.(gz|xz|zst))?([[:space:]]|$)"
'''


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
        ('kernel_module_listing="$(grep -F "lib/modules/$KVER/" <<<"$listing")"', "exact initramfs module ABI"),
        ('grep -Eq "/overlay\\\\.ko(\\\\.(gz|xz|zst))?([[:space:]]|$)" <<<"$kernel_module_listing"', "overlay kernel module gate"),
        ('grep -Fq "mount-overlayfs.sh" <<<"$listing"', "overlay mount hook gate"),
        ('grep -Fq "overlayfs" <<<"$listing"', "overlay dracut module gate"),
        ('grep -Fq "nfs" <<<"$listing"', "NFS dracut module gate"),
        ('loop: "{{ openwrt_gentoo_initramfs_builds_effective | default([]) }}"', "all-build validation loop"),
    ):
        require(initramfs_tasks, needle, label)
    if 'printf "%s\\n" "$listing" | grep' in initramfs_tasks:
        raise AssertionError("initramfs listing validation must not use a producer-to-grep pipeline")
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
        ("homecluster_check()", "bounded check marker function"),
        ("homecluster_check chroot_dev_null_ready", "chroot /dev/null check ID"),
        ('dev_null_target="$dev_dir/null"', "bounded chroot /dev/null target"),
        ('test ! -L "$dev_dir"', "chroot /dev symlink guard"),
        ('mount --bind /dev/null "$dev_null_target"', "temporary chroot /dev/null bind"),
        ('umount "$dev_null_target"', "temporary chroot /dev/null cleanup"),
        ("homecluster_check module_tree_present", "module tree check ID"),
        ('image_check_prefix="generic"', "generic image check ID prefix"),
        ('image_check_prefix="nvidia"', "NVIDIA image check ID prefix"),
        ('homecluster_check "${image_check_prefix}_initramfs_identical"', "initramfs identity check ID"),
        ('homecluster_check "${image_check_prefix}_nfs_present"', "NFS check ID"),
        ('grep -F "lib/modules/$kernel_release/" "$listing_file"', "accepted exact ABI gate"),
        ("grep -Fq 'mount-overlayfs.sh' \"$listing_file\"", "accepted overlay hook gate"),
    ):
        require(common_kernel_gate, needle, label)
    if "printf '%s\\n' \"$listing\" | grep" in common_kernel_gate:
        raise AssertionError("common-kernel gate listing validation must not use a producer-to-grep pipeline")


def expect_failure(initramfs_tasks: str, bundle_tasks: str, common_kernel_gate: str, label: str) -> None:
    try:
        validate_contract(initramfs_tasks, bundle_tasks, common_kernel_gate)
    except AssertionError:
        return
    raise AssertionError(f"negative fixture unexpectedly passed: {label}")


def validate_runtime_fixture(listing: str, *, should_pass: bool, label: str) -> None:
    completed = subprocess.run(
        ["bash", "-c", RUNTIME_VALIDATOR],
        input=listing,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if (completed.returncode == 0) != should_pass:
        raise AssertionError(f"unexpected runtime fixture result: {label}: rc={completed.returncode}")


def validate_pipeline_sigpipe_fixture(listing: str) -> None:
    completed = subprocess.run(
        ["bash", "-c", PIPELINE_RUNTIME_VALIDATOR],
        input=listing,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 141:
        raise AssertionError(f"pipeline regression fixture did not reproduce SIGPIPE: rc={completed.returncode}")


def main() -> int:
    initramfs_tasks = INITRAMFS_TASKS.read_text(encoding="utf-8")
    bundle_tasks = BUNDLE_TASKS.read_text(encoding="utf-8")
    common_kernel_gate = COMMON_KERNEL_GATE.read_text(encoding="utf-8")
    validate_contract(initramfs_tasks, bundle_tasks, common_kernel_gate)

    expect_failure(
        initramfs_tasks.replace(
            'kernel_module_listing="$(grep -F "lib/modules/$KVER/" <<<"$listing")"',
            'kernel_module_listing="$listing"',
            1,
        ),
        bundle_tasks,
        common_kernel_gate,
        "missing exact ABI check",
    )
    expect_failure(
        initramfs_tasks.replace(
            '''          kernel_module_listing="$(grep -F "lib/modules/$KVER/" <<<"$listing")"
          grep -Eq "/overlay\\\\.ko(\\\\.(gz|xz|zst))?([[:space:]]|$)" <<<"$kernel_module_listing"
          grep -Fq "mount-overlayfs.sh" <<<"$listing"
          grep -Fq "overlayfs" <<<"$listing"
          grep -Fq "nfs" <<<"$listing"''',
            '''          printf "%s\\n" "$listing" | grep -F "lib/modules/$KVER/" | grep -Eq "/overlay\\\\.ko(\\\\.(gz|xz|zst))?([[:space:]]|$)"
          printf "%s\\n" "$listing" | grep -F "mount-overlayfs.sh" >/dev/null
          printf "%s\\n" "$listing" | grep -F "overlayfs" >/dev/null
          printf "%s\\n" "$listing" | grep -F "nfs" >/dev/null''',
            1,
        ),
        bundle_tasks,
        common_kernel_gate,
        "producer-to-grep pipeline regression",
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
    expect_failure(
        initramfs_tasks,
        bundle_tasks,
        common_kernel_gate.replace("homecluster_check module_tree_present", "true", 1),
        "missing bounded module tree check ID",
    )
    expect_failure(
        initramfs_tasks,
        bundle_tasks,
        common_kernel_gate.replace('mount --bind /dev/null "$dev_null_target"', "true", 1),
        "missing temporary chroot /dev/null bind",
    )

    required_entries = [
        "usr/lib/modules/6.18.36-v8-homecluster+/kernel/fs/overlayfs/overlay.ko.xz",
        "var/lib/dracut/hooks/mount/01-mount-overlayfs.sh",
        "usr/lib/dracut/modules.d/90overlayfs/module-setup.sh",
        "usr/lib/dracut/modules.d/95nfs/module-setup.sh",
    ]
    long_listing = "\n".join(
        required_entries
        + [
            f"usr/lib/modules/6.18.36-v8-homecluster+/kernel/drivers/fixture-{index}.ko.xz"
            for index in range(20000)
        ]
    )
    validate_runtime_fixture(long_listing, should_pass=True, label="long valid lsinitrd listing")
    validate_pipeline_sigpipe_fixture(long_listing)
    validate_runtime_fixture(
        long_listing.replace(required_entries[0], "", 1),
        should_pass=False,
        label="missing overlay module",
    )
    validate_runtime_fixture(
        long_listing.replace(required_entries[1], "", 1),
        should_pass=False,
        label="missing overlay mount hook",
    )

    print("Pi5 PXE initramfs contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - emit one compact CI failure.
        print(f"Pi5 PXE initramfs contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
