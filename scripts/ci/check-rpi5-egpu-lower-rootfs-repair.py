#!/usr/bin/env python3
"""Source contract check for the guarded rpi5 eGPU lower-rootfs repair path."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_rpi5_egpu_runtime_repair"
LLM_ROLE = ROOT / "ansible/arm64/roles/rpi5_egpu_local_llm"
BUNDLE_ROLE = ROOT / "ansible/arm64/roles/rpi5_egpu_nvidia_artifact_bundle"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"missing file: {path.relative_to(ROOT)}", file=sys.stderr)
        raise


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def require_ordered(text: str, needles: tuple[str, ...], label: str) -> None:
    positions = [text.find(needle) for needle in needles]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError(f"invalid {label}: {' -> '.join(needles)}")


def require_not(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"forbidden {label}: {needle}")


def controller_transfer_block(playbook: str) -> str:
    start = "    - name: rpi5 eGPU generation contract を controller fact へ転送\n"
    end = "      when: pxe_release_bundle_rpi5_nvidia_tftp_required | bool\n"
    if playbook.count(start) != 1:
        raise AssertionError("controller generation contract transfer must be declared exactly once")
    _, remainder = playbook.split(start, 1)
    if end not in remainder:
        raise AssertionError("controller generation contract transfer has no bounded when clause")
    return remainder.split(end, 1)[0]


def main() -> int:
    defaults = read(ROLE / "defaults/main.yml")
    llm_defaults = read(LLM_ROLE / "defaults/main.yml")
    bundle_defaults = read(BUNDLE_ROLE / "defaults/main.yml")
    bundle_tasks = read(BUNDLE_ROLE / "tasks/main.yml")
    main_tasks = read(ROLE / "tasks/main.yml")
    preflight = read(ROLE / "tasks/preflight.yml")
    repair = read(ROLE / "tasks/repair.yml")
    repair_staged = read(ROLE / "tasks/repair_from_staged_source_rootfs.yml")
    verify = read(ROLE / "tasks/verify.yml")
    site = read(ROOT / "ansible/openwrt/site.yml")
    release_bundle_build = read(ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml")
    release_bundle = read(ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/release_bundle.yml")
    staging_playbook = read(ROOT / "ansible/openwrt/playbooks/pxe-release-bundle-staging.yml")
    controller_transfer = controller_transfer_block(staging_playbook)
    bundle_playbook = read(ROOT / "ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml")

    require(defaults, "openwrt_rpi5_egpu_runtime_repair_enabled: false", "disabled default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_apply: false", "apply disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_enabled: false", "generation disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_apply: false", "generation apply disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_manifest_metadata: {}", "empty generation metadata default")
    require(defaults, "openwrt_rpi5_egpu_generation_artifact_bundle_enabled: false", "artifact bundle disabled default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_target_rootfs: \"\"", "empty target rootfs default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_use_staged_source_rootfs: false", "staged source disabled default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_source_rootfs: \"\"", "empty source rootfs default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_kernel_version: \"\"", "empty kernel version default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_nvidia_driver_version: \"\"", "empty driver version default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_open_kernel_modules_commit: \"\"", "empty module commit default")
    require(defaults, "--no-kernel-modules", "userspace-only NVIDIA install")
    require(
        llm_defaults,
        "rpi5_egpu_llama_revision: a4107133a634250c8c9d888bc0bc8520dcfd6105",
        "known-good llama.cpp revision",
    )
    require(bundle_defaults, "rpi5_egpu_nvidia_artifact_bundle_enabled: false", "native bundle disabled default")
    require(bundle_defaults, "rpi5_egpu_nvidia_artifact_bundle_apply: false", "native bundle apply disabled default")
    require(bundle_defaults, "/var/lib/rancher/k3s/nvidia-artifacts", "native bundle local SSD output")
    require(bundle_tasks, "rpi5_egpu_nvidia_artifact_bundle_confirm ==", "native bundle confirm guard")
    require(bundle_tasks, "rm -rf \"$work\"", "native bundle work cleanup")
    require(bundle_tasks, "rm -rf \"$work/payload/linux/.git\"", "native bundle excludes kernel git")
    require(bundle_tasks, "manifest.sha256", "native bundle payload manifest")
    require(bundle_tasks, "archive_tmp=\"${archive}.tmp\"", "native bundle temporary archive")
    require(bundle_tasks, "gzip -t \"$archive_tmp\"", "native bundle gzip validation")
    require(bundle_tasks, "tar -tzf \"$archive_tmp\" >/dev/null", "native bundle tar validation")
    require(bundle_tasks, "printf '%s  %s\\n' \"$checksum\" \"$archive\" > \"$manifest_tmp\"", "native bundle final archive manifest")
    require(bundle_playbook, "hosts: rpi5_egpu_artifact_bundle", "native bundle inventory group")
    for atom in (
        "media-libs/vulkan-loader",
        "dev-util/vulkan-tools",
        "media-libs/shaderc",
        "x11-libs/libX11",
        "x11-libs/libXext",
        "sys-apps/pciutils",
    ):
        require(defaults, atom, f"required package {atom}")

    require(main_tasks, "include_tasks: preflight.yml", "preflight include")
    require(main_tasks, "include_tasks: repair.yml", "repair include")
    require(main_tasks, "include_tasks: repair_from_staged_source_rootfs.yml", "staged source repair include")
    require(main_tasks, "openwrt_rpi5_egpu_runtime_repair_apply | bool", "repair apply gate")
    require(main_tasks, "include_tasks: verify.yml", "verify include")

    require(preflight, "openwrt_rpi5_egpu_runtime_repair_confirm_expected", "confirm expected")
    require(preflight, "openwrt_rpi5_egpu_runtime_repair_confirm ==", "confirm assert")
    require(preflight, "is match('.+-v8-nvidia\\\\+$')", "nvidia kernel suffix assert")
    require(preflight, "open_kernel_modules_commit", "module commit guard")
    require(preflight, "openwrt_rpi5_egpu_runtime_repair_source_rootfs_resolved", "source rootfs guard")

    require(repair, "emerge {{ openwrt_rpi5_egpu_runtime_repair_emerge_args | join(' ') }}", "vulkan package install")
    require(repair, "rpi5 eGPU target rootfs dev mountpoint を作成", "target rootfs dev mountpoint")
    require(repair, "rpi5 eGPU target rootfs proc mountpoint を作成", "target rootfs proc mountpoint")
    require(repair, "rpi5 eGPU target rootfs artifact payload mountpoint を作成", "target rootfs artifact mountpoint")
    require(repair, "rpi5 eGPU target rootfs へ artifact payload を bind mount", "target rootfs artifact bind mount")
    require(repair, "openwrt_rpi5_egpu_runtime_repair_nvidia_runfile_path_resolved | dirname", "artifact source directory")
    require(repair, "/.homecluster-rpi5-egpu-artifact/", "artifact in-chroot path")
    require_not(
        repair,
        "{{ openwrt_rpi5_egpu_runtime_repair_nvidia_runfile_path_resolved }}\n"
        "            {{ openwrt_rpi5_egpu_runtime_repair_nvidia_runfile_args | join(' ') }}",
        "host artifact path executed inside chroot",
    )
    require(repair, "/bin/mount", "target rootfs dev bind mount command")
    require(repair, "rbind", "target rootfs recursive dev bind mount")
    require(repair, "always:", "target rootfs dev bind mount cleanup block")
    require(repair, "/bin/umount", "target rootfs dev bind mount cleanup command")
    require(repair, "failed_when: false", "target rootfs dev bind mount cleanup failure tolerance")
    require_ordered(
        repair,
        (
            "rpi5 eGPU target rootfs へ host /dev を recursive bind mount",
            "rpi5 eGPU target rootfs へ host /proc を recursive bind mount",
            "rpi5 eGPU target rootfs へ artifact payload を bind mount",
            "rpi5 eGPU Vulkan packages を target rootfs に導入",
        ),
        "lower-rootfs pseudo-filesystem setup order",
    )
    require_ordered(
        repair,
        (
            "rpi5 eGPU target rootfs の artifact payload bind mount を解除",
            "rpi5 eGPU target rootfs の artifact payload mountpoint を削除",
            "rpi5 eGPU target rootfs の host /proc bind mount を解除",
            "rpi5 eGPU target rootfs の host /dev bind mount を解除",
        ),
        "lower-rootfs pseudo-filesystem cleanup order",
    )
    require(repair, "openwrt_rpi5_egpu_runtime_repair_nvidia_runfile_args | join(' ')", "NVIDIA runfile args")
    require(repair, "rpi5 eGPU prebuilt NVIDIA open kernel modules を target rootfs に配置", "prebuilt module stage")
    require(repair, "executable: /bin/ash", "OpenWrt ash module stage")
    require(
        repair,
        "openwrt_rpi5_egpu_runtime_repair_open_kernel_modules_source_dir_resolved }}/kernel-open",
        "prebuilt module source",
    )
    require(repair, "/kernel/drivers/video", "target-root NVIDIA module directory")
    for module in ("nvidia.ko", "nvidia-modeset.ko", "nvidia-drm.ko", "nvidia-uvm.ko", "nvidia-peermem.ko"):
        require(repair, module, f"prebuilt NVIDIA module {module}")
    require(repair, "cp -f \"$source/$module\" \"$target/$module\"", "prebuilt module copy")
    require_not(repair, "git rev-parse HEAD", "OpenWrt artifact source must not require git metadata")
    require_not(repair, "make modules", "OpenWrt must not build NVIDIA modules during generation")
    require_not(repair, "make modules_install", "OpenWrt must not build NVIDIA modules during generation")
    require(repair, "depmod -a", "target depmod")

    require(repair_staged, "openwrt_rpi5_egpu_runtime_repair_source_rootfs_resolved", "staged source rootfs")
    require(repair_staged, "CONTENTS", "package contents copy")
    require(repair_staged, "nvidia*.ko*", "staged module copy")
    require(repair_staged, "nvidia_icd.json", "staged ICD copy")
    require(repair_staged, "gsp*.bin", "staged GSP firmware copy")
    require(repair_staged, "depmod -a", "staged target depmod")

    require(verify, "nvidia_icd.json", "ICD verification")
    require(verify, "gsp_ga10x.bin", "GSP firmware verification")
    require(verify, "nvidia*.ko*", "kernel module verification")
    require(verify, "libGLX_nvidia.so", "userspace verification")
    require(verify, "var/db/pkg", "VDB package verification")
    require(verify, "depmod -n", "depmod verification")

    require(site, "name: openwrt_rpi5_egpu_runtime_repair", "site include_role")
    require(site, "rpi5_egpu_runtime_repair", "site tag")
    require(site, "- never", "never tag present")

    require(release_bundle_build, "rpi5 eGPU generation build の対象 release を解決", "generation release resolution")
    require(release_bundle_build, "pxe_release_bundle_rpi5_items | length == 1", "single rpi5 release guard")
    require(release_bundle_build, "openwrt_rpi5_egpu_generation_apply", "generation apply guard")
    require(release_bundle_build, "artifact source archive を取得前に検証", "artifact source preflight")
    require(
        release_bundle_build,
        "hostvars[inventory_hostname].openwrt_rpi5_egpu_generation_artifact_source_host",
        "artifact source controller-scoped delegated user",
    )
    require(
        release_bundle_build,
        "hostvars[hostvars[inventory_hostname].openwrt_rpi5_egpu_generation_artifact_source_host]",
        "artifact source delegated host user",
    )
    require(release_bundle_build, "openwrt_rpi5_egpu_generation_artifact_source_host | default(\"\", true) | string | trim | length > 0", "artifact source host fail-closed guard")
    require(release_bundle_build, "sha256sum -c \"$manifest\"", "artifact source checksum validation")
    require(release_bundle_build, "gzip -t \"$archive\"", "artifact source gzip validation")
    require(release_bundle_build, "tar -tzf \"$archive\" >/dev/null", "artifact source tar validation")
    require(release_bundle_build, "ansible_remote_tmp: /tmp/.ansible-tmp", "artifact source remote tmp")
    require(release_bundle_build, "artifact archive を controller へ取得", "artifact fetch")
    require(release_bundle_build, "artifact archive checksum を controller で検証", "artifact controller checksum")
    require(release_bundle_build, "gzip -t \"$archive\"", "artifact controller gzip validation")
    require(release_bundle_build, "tar -tzf \"$archive\" >/dev/null", "artifact controller tar validation")
    require(release_bundle_build, "artifact payload checksum を OpenWrt で検証", "artifact router checksum")
    require(release_bundle_build, "kernel modules を target rootfs へ配置", "artifact module stage")
    require(release_bundle_build, "set -eu\n    stage=\"{{ openwrt_rpi5_egpu_generation_artifact_openwrt_dir }}", "OpenWrt shell-compatible module stage")
    require(release_bundle_build, "executable: /bin/sh\n  changed_when: true", "OpenWrt module stage shell")
    require(release_bundle_build, "NVIDIA initramfs を再生成", "forced NVIDIA initramfs")
    for key in (
        "kernel_release",
        "nvidia_driver_version",
        "nvidia_runfile_sha256",
        "open_kernel_modules_commit",
        "icd_path",
        "icd_sha256",
        "llama_cpp_revision",
    ):
        require(release_bundle_build, key, f"generation provenance {key}")
    require(release_bundle_build, "name: openwrt_rpi5_egpu_runtime_repair", "generation role include")
    require(release_bundle_build, "openwrt_rpi5_egpu_runtime_repair_target_rootfs", "generation target rootfs")
    require(release_bundle, "metadata: \"{{ openwrt_gentoo_release_bundle_manifest_metadata | default({}) }}\"", "manifest metadata")
    require(staging_playbook, "pxe_release_bundle_manifest_metadata_base", "staging manifest metadata forwarding")
    require(staging_playbook, "rpi5_nvidia", "staging NVIDIA manifest forwarding")
    require(staging_playbook, "PXE client catalog を合成", "staging PXE catalog preflight")
    require(staging_playbook, "pxe_release_bundle_rpi5_nvidia_tftp_required", "NVIDIA TFTP requirement fact")
    if staging_playbook.count("rpi5 eGPU NVIDIA TFTP artifact requirement を判定") != 1:
        raise AssertionError("NVIDIA TFTP requirement fact must be declared exactly once")
    if staging_playbook.count("rpi5 eGPU NVIDIA TFTP artifact requirement と generation opt-in を検証") != 1:
        raise AssertionError("NVIDIA TFTP opt-in guard must be declared exactly once")
    for invalid_fragment in ("default(, true)", "-nvidia. in path"):
        if invalid_fragment in staging_playbook:
            raise AssertionError(f"invalid NVIDIA TFTP guard fragment: {invalid_fragment}")
    for field in (
        "host.rpi5_kernel_image",
        "host.rpi5_initramfs",
        "host.rpi5_device_tree",
    ):
        require(staging_playbook, field, f"NVIDIA TFTP requirement field {field}")
    for flag in (
        "openwrt_rpi5_egpu_generation_enabled",
        "openwrt_rpi5_egpu_generation_apply",
        "openwrt_rpi5_egpu_generation_artifact_bundle_enabled",
    ):
        require(staging_playbook, flag, f"NVIDIA TFTP requirement opt-in {flag}")
    for field in (
        "openwrt_rpi5_egpu_runtime_repair_kernel_version",
        "openwrt_rpi5_egpu_runtime_repair_nvidia_driver_version",
        "openwrt_rpi5_egpu_runtime_repair_open_kernel_modules_commit",
        "openwrt_rpi5_egpu_runtime_repair_confirm",
        "openwrt_rpi5_egpu_runtime_repair_confirm_expected",
    ):
        require(controller_transfer, f"{field}: >-", f"rpi5 eGPU controller transfer {field}")
        require(
            controller_transfer,
            f"hostvars[pxe_release_bundle_rpi5_nvidia_client_names[0]].{field}",
            f"rpi5 eGPU controller hostvars transfer {field}",
        )
    require(
        staging_playbook,
        "not (pxe_release_bundle_rpi5_nvidia_tftp_required | bool)",
        "NVIDIA TFTP requirement negative path",
    )

    print("rpi5 eGPU lower-rootfs repair contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CI helper should print compact failure.
        print(f"rpi5 eGPU lower-rootfs repair contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
