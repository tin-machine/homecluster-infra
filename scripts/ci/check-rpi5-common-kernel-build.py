#!/usr/bin/env python3
"""Source contract check for the guarded Raspberry Pi 5 common kernel build."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROLE = ROOT / "ansible/arm64/roles/rpi5_common_kernel_build"
BUNDLE_ROLE = ROOT / "ansible/arm64/roles/rpi5_egpu_nvidia_artifact_bundle"
REPAIR_ROLE = ROOT / "ansible/openwrt/roles/openwrt_rpi5_egpu_runtime_repair"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"missing file: {path.relative_to(ROOT)}", file=sys.stderr)
        raise


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def require_not(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"forbidden {label}: {needle}")


def main() -> int:
    defaults = read(BUILD_ROLE / "defaults/main.yml")
    tasks = read(BUILD_ROLE / "tasks/main.yml")
    bundle_tasks = read(BUNDLE_ROLE / "tasks/main.yml")
    repair_preflight = read(REPAIR_ROLE / "tasks/preflight.yml")
    bundle_playbook = read(ROOT / "ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml")
    staging_entrypoint = read(
        ROOT / "ansible/openwrt/playbooks/pxe-release-bundle-staging-with-common-kernel.yml"
    )
    rootfs_tasks = read(ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/portage_chroot.yml")

    require(defaults, "rpi5_common_kernel_build_enabled: false", "disabled default")
    require(defaults, "rpi5_common_kernel_build_apply: false", "apply disabled default")
    require(defaults, "rpi5_common_kernel_build_confirm_expected: \"\"", "empty confirmation")
    require(defaults, "rpi5_common_kernel_build_localversion: -v8-homecluster", "common suffix")
    require(defaults, "/var/lib/rancher/k3s/kernel-build", "local SSD work root")
    require(defaults, "rpi5_common_kernel_build_distcc_enabled: true", "distcc enabled default")
    require(defaults, "Plain distcc only", "plain distcc policy")

    require(tasks, "kernel/config_data.gz", "local generated config data prebuild")
    require(tasks, "kernel/configs.o", "local generated config object prebuild")
    prepare_index = tasks.index("- make\n      - prepare")
    config_data_index = tasks.index(
        "- name: Rpi5 common kernel build の kernel/config_data.gz を local で先行生成"
    )
    configs_object_index = tasks.index(
        "- name: Rpi5 common kernel build の kernel/configs.o を wrapper経由で先行生成"
    )
    if not prepare_index < config_data_index < configs_object_index:
        raise AssertionError("local generated config preparation order is not preserved")
    wrapper_index = tasks.index(".homecluster-cc-wrapper")
    if not prepare_index < wrapper_index < config_data_index:
        raise AssertionError("generated config compiler wrapper order is not preserved")
    configs_task = tasks[configs_object_index : tasks.index("plain distccでbuild")]
    require(configs_task, "CC: ./.homecluster-cc-wrapper", "local configs compiler wrapper")
    require(tasks, "*) exec distcc gcc", "plain distcc compiler wrapper path")
    require(tasks, "-o kernel/configs.o", "local generated config compiler wrapper target")

    require(rootfs_tasks, "sys-devel/bc", "k3s_base_baseline_packages")
    require(rootfs_tasks, "k3s_base_baseline_packages:", "k3s_base_baseline_packages definition")

    for config_gate in (
        "scripts/config --enable ARM64_4K_PAGES",
        "scripts/config --disable ARM64_16K_PAGES",
        "scripts/config --disable ARM64_64K_PAGES",
        "scripts/config --enable ARM64_VA_BITS_48",
        "scripts/config --set-val ARM64_VA_BITS 48",
        "scripts/config --enable PCIE_BRCMSTB",
        "scripts/config --enable PCI_MSI",
        "scripts/config --set-str LOCALVERSION",
        "scripts/config --disable LOCALVERSION_AUTO",
    ):
        require(tasks, config_gate, f"kernel config gate {config_gate}")

    for distcc_gate in (
        "systemctl is-enabled distccd",
        "systemctl is-active distccd",
        "ss -H -ltn",
        "gcc -dumpmachine",
        "gcc -dumpfullversion -dumpversion",
        "as --version",
        "DISTCC_FALLBACK",
        "DISTCC_IO_TIMEOUT",
        "CC='{{ './.homecluster-cc-wrapper'",
        "'--pump' not in rpi5_common_kernel_build_distcc_hosts_effective",
    ):
        require(tasks, distcc_gate, f"distcc gate {distcc_gate}")

    require(tasks, "make modules_install INSTALL_MOD_PATH=", "staged modules install")
    require(tasks, "cp -a .config Module.symvers System.map vmlinux", "build provenance copy")
    require(tasks, "git reset --hard \"{{ rpi5_common_kernel_build_open_modules_commit }}\"", "clean NVIDIA worktree pin")
    require(tasks, "git clean -ffdx", "clean NVIDIA worktree artifacts")
    require(tasks, "make clean", "clean NVIDIA module artifacts")
    require(tasks, "make -j{{ rpi5_common_kernel_build_nvidia_module_jobs", "NVIDIA module build")
    require(tasks, "SYSSRC=\"{{ rpi5_common_kernel_build_dir }}\"", "NVIDIA common kernel source")
    require(tasks, "TARGET_ARCH=aarch64", "NVIDIA target architecture")
    require(tasks, "modinfo -F vermagic", "NVIDIA module vermagic inspection")
    require(tasks, '"${expected_release}"*', "NVIDIA module kernel release match")
    require(defaults, "  - modinfo", "modinfo required command")
    require(tasks, "rpi5_common_kernel_build_manifest_metadata", "manifest metadata fact")
    require(tasks, "page_size: 4096", "4K metadata")
    require(tasks, "va_bits: 48", "48-bit metadata")

    require(bundle_playbook, "name: ../roles/rpi5_common_kernel_build", "common build role include")
    require(bundle_playbook, "name: ../roles/rpi5_egpu_nvidia_artifact_bundle", "bundle role include")
    require(staging_entrypoint, "rpi5-egpu-nvidia-artifact-bundle.yml", "builder pre-play import")
    require(staging_entrypoint, "pxe-release-bundle-staging.yml", "existing staging import")

    for text, label in (
        (bundle_tasks, "artifact bundle"),
        (repair_preflight, "lower-rootfs repair"),
    ):
        require(text, ".+-v8-homecluster\\\\+", f"{label} common suffix")
        require(text, ".+-v8-nvidia\\\\+", f"{label} legacy suffix")

    require(bundle_tasks, "kernel8-homecluster.img", "canonical kernel alias")
    require(bundle_tasks, "bcm2712-rpi-5-b-homecluster.dtb", "canonical DTB alias")
    require(bundle_tasks, "openwrt_rpi5_egpu_generation_artifact_archive_path", "generation archive fact")
    require(bundle_tasks, "openwrt_rpi5_egpu_runtime_repair_kernel_version", "generation kernel fact")
    require_not(
        repair_preflight,
        "rpi5_egpu_nvidia_artifact_bundle_inputs.results",
        "cross-role register reference",
    )

    print("rpi5 common kernel build contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CI helper should print compact failure.
        print(f"rpi5 common kernel build contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
