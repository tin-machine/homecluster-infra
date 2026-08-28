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


def validate_generation_entrypoint_release_date(text: str) -> None:
    require(text, "pxe_release_bundle_stage: stg", "staging PXE release scope")
    require(text, "openwrt_gentoo_release_bundle_stage_dates", "external inventory PXE release date source")
    require(text, "pxe_release_bundle_date:", "explicit PXE release date wiring")
    require_not(text, "rpi5_common_kernel_build_stage_date", "kernel artifact date coupled to PXE release date")


def main() -> int:
    defaults = read(BUILD_ROLE / "defaults/main.yml")
    tasks = read(BUILD_ROLE / "tasks/main.yml")
    bundle_defaults = read(BUNDLE_ROLE / "defaults/main.yml")
    bundle_tasks = read(BUNDLE_ROLE / "tasks/main.yml")
    repair_preflight = read(REPAIR_ROLE / "tasks/preflight.yml")
    bundle_playbook = read(ROOT / "ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml")
    generation_entrypoint = read(ROOT / "ansible/openwrt/playbooks/pxe-release-bundle-staging-with-common-kernel.yml")
    precheck = read(ROOT / "ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml")
    gate = read(ROOT / "ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml")
    rootfs_tasks = read(ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/portage_chroot.yml")

    require(defaults, "rpi5_common_kernel_build_enabled: false", "disabled default")
    require(defaults, "rpi5_common_kernel_build_apply: false", "apply disabled default")
    require(defaults, "rpi5_common_kernel_build_confirm_expected: \"\"", "empty confirmation")
    require(defaults, "rpi5_common_kernel_build_localversion: -v8-homecluster", "common suffix")
    require(defaults, "/var/lib/rancher/k3s/kernel-build", "local SSD work root")
    require(defaults, "rpi5_common_kernel_build_distcc_enabled: true", "distcc enabled default")
    require(defaults, "Plain distcc only", "plain distcc policy")

    require(tasks, "kernel/configs.o", "local generated config object prebuild")
    require_not(tasks, "make\n      - kernel/config_data.gz", "direct config data prebuild target")
    prepare_index = tasks.index("- make\n      - prepare")
    configs_object_index = tasks.index("- name: Rpi5 common kernel build の kernel/configs.o を wrapper経由で先行生成")
    if not prepare_index < configs_object_index:
        raise AssertionError("local generated config preparation order is not preserved")
    wrapper_index = tasks.index(".homecluster-cc-wrapper")
    if not prepare_index < wrapper_index < configs_object_index:
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
    require(tasks, "NVIDIA external modules worktree の所有者を builder に戻す", "NVIDIA worktree ownership repair")
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
    require_not(bundle_playbook, "rpi5_common_kernel_stage_date_from_openwrt", "PXE-derived kernel build date")
    require_not(bundle_playbook, "rpi5_common_kernel_build_stage_date:", "caller stage-date override")
    require(bundle_playbook, "PXE release date is resolved independently", "identity separation explanation")
    require(tasks, "rpi5_common_kernel_build_bundle_output", "common kernel bundle output fact")
    require(
        tasks,
        'metadata_path: "{{ rpi5_common_kernel_build_metadata_path }}"',
        "metadata path in common kernel bundle output",
    )
    require(
        bundle_defaults,
        'rpi5_egpu_nvidia_artifact_bundle_metadata_path: ""',
        "bundle metadata path input default",
    )
    require(
        bundle_playbook,
        'rpi5_egpu_nvidia_artifact_bundle_metadata_path: "{{ rpi5_common_kernel_build_bundle_output.metadata_path }}"',
        "explicit metadata path wiring",
    )
    require(generation_entrypoint, "rpi5-egpu-nvidia-artifact-bundle.yml", "builder pre-play import")
    require(generation_entrypoint, "pxe-release-bundle-build.yml", "build-only PXE bundle import")
    validate_generation_entrypoint_release_date(generation_entrypoint)
    require_not(generation_entrypoint, "pxe-release-bundle-staging.yml", "staging promote import")

    for text, label in ((bundle_tasks, "artifact bundle"), (repair_preflight, "lower-rootfs repair")):
        require(text, ".+-v8-homecluster\\\\+", f"{label} common suffix")
        require(text, ".+-v8-nvidia\\\\+", f"{label} legacy suffix")

    require(bundle_tasks, "kernel8-homecluster.img", "canonical kernel alias")
    require(bundle_tasks, "bcm2712-rpi-5-b-homecluster.dtb", "canonical DTB alias")
    require(bundle_tasks, "openwrt_rpi5_egpu_generation_artifact_archive_path", "generation archive fact")
    require(bundle_tasks, "openwrt_rpi5_egpu_runtime_repair_kernel_version", "generation kernel fact")
    require(bundle_tasks, "rpi5_common_kernel_artifact_identity", "content-addressed artifact identity")
    require(bundle_tasks, "common_kernel_artifact", "release manifest artifact reference metadata")
    require(bundle_tasks, 'id: "sha256:', "sha256 artifact id")
    require(
        bundle_tasks,
        '"{{ rpi5_egpu_nvidia_artifact_bundle_metadata_path }}"',
        "bundle metadata path input use",
    )
    require_not(
        bundle_tasks,
        '"{{ rpi5_common_kernel_build_metadata_path }}"',
        "cross-role metadata path reference",
    )
    require_not(repair_preflight, "rpi5_egpu_nvidia_artifact_bundle_inputs.results", "cross-role register reference")

    require_not(precheck, "homecluster_common_kernel_stg_stage_date_from_openwrt", "stage-date equality precheck")
    require(precheck, "PXE release identity", "independent PXE release validation")
    source_repo_default = next(
        line.split(":", 1)[1].strip()
        for line in defaults.splitlines()
        if line.startswith("rpi5_common_kernel_build_source_repo:")
    )
    require(
        precheck,
        f"homecluster_common_kernel_build_source_repo_default: {source_repo_default}",
        "precheck source repository default",
    )
    require(
        precheck,
        "| default(homecluster_common_kernel_build_source_repo_default, true)",
        "precheck source repository fallback",
    )
    require(precheck, "ansible.builtin.command:", "source remote command argv")
    require(precheck, "- ls-remote", "source remote reachability check")
    require(gate, "common_kernel_artifact", "PXE manifest artifact reference")
    require(gate, "kernel_artifact_id", "gate artifact identity output")
    require(gate, "pxe_release_id", "gate PXE identity output")
    require(gate, "pxe_release_manifest_sha256", "PXE manifest hash")
    require(gate, "rpi5-common-kernel-gate-v2", "gate schema v2")

    print("rpi5 common kernel build contract ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"rpi5 common kernel build contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
