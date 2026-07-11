#!/usr/bin/env python3
"""Source contract check for the guarded rpi5 eGPU lower-rootfs repair path."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_rpi5_egpu_runtime_repair"
LLM_ROLE = ROOT / "ansible/arm64/roles/rpi5_egpu_local_llm"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"missing file: {path.relative_to(ROOT)}", file=sys.stderr)
        raise


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def main() -> int:
    defaults = read(ROLE / "defaults/main.yml")
    llm_defaults = read(LLM_ROLE / "defaults/main.yml")
    main_tasks = read(ROLE / "tasks/main.yml")
    preflight = read(ROLE / "tasks/preflight.yml")
    repair = read(ROLE / "tasks/repair.yml")
    repair_staged = read(ROLE / "tasks/repair_from_staged_source_rootfs.yml")
    verify = read(ROLE / "tasks/verify.yml")
    site = read(ROOT / "ansible/openwrt/site.yml")
    release_bundle_build = read(ROOT / "ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml")
    release_bundle = read(ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/release_bundle.yml")
    staging_playbook = read(ROOT / "ansible/openwrt/playbooks/pxe-release-bundle-staging.yml")

    require(defaults, "openwrt_rpi5_egpu_runtime_repair_enabled: false", "disabled default")
    require(defaults, "openwrt_rpi5_egpu_runtime_repair_apply: false", "apply disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_enabled: false", "generation disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_apply: false", "generation apply disabled default")
    require(defaults, "openwrt_rpi5_egpu_generation_manifest_metadata: {}", "empty generation metadata default")
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
    require(repair, "openwrt_rpi5_egpu_runtime_repair_nvidia_runfile_args | join(' ')", "NVIDIA runfile args")
    require(repair, "test \"$current_commit\" =", "open kernel module commit check")
    require(repair, "INSTALL_MOD_PATH=", "target-root module install")
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

    print("rpi5 eGPU lower-rootfs repair contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CI helper should print compact failure.
        print(f"rpi5 eGPU lower-rootfs repair contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
