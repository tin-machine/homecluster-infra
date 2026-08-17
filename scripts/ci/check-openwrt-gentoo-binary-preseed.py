#!/usr/bin/env python3
"""Validate the OpenWrt Gentoo binary-only preseed source contract."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/defaults/main.yml"
CHROOT_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/portage_chroot.yml"
PREPARE_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/portage_prepare.yml"
MAKE_CONF_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/portage/make.conf.j2"
CONTRACT_PATH = REPO_ROOT / "docs/site-input-contract.md"

EXPECTED_PACKAGES = (
    "dev-lang/rust-bin",
    "dev-lang/go",
    "dev-util/maturin",
    "sys-devel/gcc",
    "app-shells/fish",
    "app-editors/neovim",
    "net-libs/nodejs",
)

OFFICIAL_SETUP_TASKS = (
    "official binhost 設定を検証",
    "official binhost 対象 rootfs の存在を確認",
    "official binhost 対象 rootfs の存在を検証",
    "official binhost binrepos.conf ディレクトリを作成",
    "official binhost binrepos.conf を配置",
)

ORDERED_TASKS = (
    "official binhost パッケージを rootfs に事前導入",
    "heavy prebuilt の unversioned binary-only contract を検証",
    "source build 版 Rust が prebuilt package list に混入していないことを確認",
    "source build 版 Rust を world から外す",
    "heavy prebuilt パッケージを configured binpkg repositories から事前導入",
    "Gentoo Portage を profile default Python 対応へ先行移行",
    "Gentoo Python target を profile default へ移行",
    "Gentoo パッケージをインストール (sudo / nfs-utils / openssh / ansible-pull / dracut / rpi-eeprom / rpi-wireless)",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def task_block(text: str, name: str) -> str:
    marker = f"    - name: {name}\n"
    if text.count(marker) != 1:
        raise ValueError(f"task must exist exactly once: {name}")
    start = text.index(marker)
    end = text.find("\n    - name: ", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


def when_section(block: str) -> str:
    marker = "\n      when:\n"
    if marker not in block:
        raise ValueError("task has no when section")
    section = block.split(marker, 1)[1]
    return section.split("\n      tags:\n", 1)[0]


def extract_packages(defaults: str) -> tuple[str, ...]:
    match = re.search(
        r"^openwrt_gentoo_heavy_prebuilt_packages:\n((?:  - [^\n]+\n)+)",
        defaults,
        re.MULTILINE,
    )
    if not match:
        return ()
    return tuple(line.removeprefix("  - ") for line in match.group(1).splitlines())


def extract_args(defaults: str) -> str:
    match = re.search(
        r'^openwrt_gentoo_heavy_prebuilt_emerge_args:\s*"([^"]+)"\s*$',
        defaults,
        re.MULTILINE,
    )
    return match.group(1) if match else ""


def validate(
    defaults: str, chroot: str, prepare: str, make_conf: str, contract: str
) -> list[str]:
    failures: list[str] = []

    packages = extract_packages(defaults)
    if packages != EXPECTED_PACKAGES:
        failures.append("heavy package defaults must be the seven code-owned unversioned atoms")
    invalid_atoms = [
        atom
        for atom in packages
        if not re.fullmatch(r"[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+", atom)
        or re.search(r"-[0-9]", atom)
    ]
    if invalid_atoms:
        failures.append("heavy package defaults contain a versioned or unsafe atom")

    args = extract_args(defaults)
    tokens = args.split()
    for required in ("--getbinpkgonly", "--update", "--binpkg-respect-use=y", "--changed-use"):
        if required not in tokens:
            failures.append(f"heavy emerge args must include {required}")
    if "--usepkgonly" in tokens:
        failures.append("heavy emerge args must not use local-only --usepkgonly")

    for name in OFFICIAL_SETUP_TASKS:
        try:
            condition = when_section(task_block(chroot, name))
        except ValueError as exc:
            failures.append(str(exc))
            continue
        if "openwrt_gentoo_official_binhost_enabled | bool" not in condition:
            failures.append(f"official binhost setup task is not enabled-gated: {name}")
        if "openwrt_gentoo_official_binhost_packages" in condition:
            failures.append(f"official binhost setup still depends on the legacy package list: {name}")

    try:
        legacy_when = when_section(
            task_block(chroot, "official binhost パッケージを rootfs に事前導入")
        )
        if "openwrt_gentoo_official_binhost_enabled | bool" not in legacy_when:
            failures.append("legacy official preseed must remain enabled-gated")
        if "openwrt_gentoo_official_binhost_packages | length > 0" not in legacy_when:
            failures.append("legacy official preseed must remain package-list-gated")
    except ValueError as exc:
        failures.append(str(exc))

    positions: list[int] = []
    for name in ORDERED_TASKS:
        marker = f"    - name: {name}\n"
        if chroot.count(marker) != 1:
            failures.append(f"ordered task must exist exactly once: {name}")
            positions.append(-1)
        else:
            positions.append(chroot.index(marker))
    if all(position >= 0 for position in positions) and positions != sorted(positions):
        failures.append("binary preseed must run before Python migration and runtime emerge")

    try:
        getuto_init = task_block(
            chroot, "Portage binpkg GPG trust anchor を getuto で初期化"
        )
        for fragment in (
            "/usr/bin/getuto",
            "creates: \"{{ openwrt_gentoo_rootfs_dir }}/etc/portage/gnupg/mykeyid\"",
            "when: openwrt_gentoo_official_binhost_enabled | bool",
        ):
            if fragment not in getuto_init:
                failures.append(f"getuto initialization task is missing: {fragment}")
        if positions[0] >= 0 and chroot.index("/usr/bin/getuto") > positions[0]:
            failures.append("getuto trust anchor must be initialized before heavy preseed")
    except ValueError as exc:
        failures.append(str(exc))

    try:
        getuto_verify = task_block(
            chroot, "Portage binpkg GPG trust anchor を検証"
        )
        for fragment in (
            "test -s /etc/portage/gnupg/mykeyid",
            "test -r /etc/portage/gnupg/trustdb.gpg",
            "when: openwrt_gentoo_official_binhost_enabled | bool",
        ):
            if fragment not in getuto_verify:
                failures.append(f"getuto verification task is missing: {fragment}")
    except ValueError as exc:
        failures.append(str(exc))

    try:
        binary_assert = task_block(
            chroot, "heavy prebuilt の unversioned binary-only contract を検証"
        )
        required_assert_fragments = (
            "openwrt_gentoo_heavy_prebuilt_emerge_args is string",
            "'--getbinpkgonly' in",
            "'--usepkgonly' not in",
            "^[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+$",
            ".*-[0-9].*",
            "openwrt_gentoo_heavy_prebuilt_enabled | bool",
        )
        for fragment in required_assert_fragments:
            if fragment not in binary_assert:
                failures.append(f"binary-only runtime assert is missing: {fragment}")
    except ValueError as exc:
        failures.append(str(exc))

    if "or (openwrt_gentoo_official_binhost_enabled | bool)" not in prepare:
        failures.append("chroot work gate must include official binhost without a package-list dependency")
    if "(openwrt_gentoo_heavy_prebuilt_enabled | bool)" not in prepare:
        failures.append("chroot work gate must include heavy preseed")
    if re.search(
        r"openwrt_gentoo_official_binhost_enabled[^\n]+\n\s+and \(\(openwrt_gentoo_official_binhost_packages",
        prepare,
    ):
        failures.append("chroot work gate still couples official binhost to the legacy package list")

    for fragment in ('BINPKG_GPG_VERIFY_GPG_HOME="/etc/portage/gnupg"',):
        if fragment not in make_conf:
            failures.append(f"binpkg GPG verification home contract is missing: {fragment}")

    for fragment in (
        "GPG_VERIFY_USER_DROP=",
        "GPG_VERIFY_GROUP_DROP=",
    ):
        if fragment in make_conf:
            failures.append(f"binpkg GPG verification overrides Gentoo privilege drop: {fragment}")

    for fragment in (
        "/usr/bin/getuto",
        "/etc/portage/gnupg/mykeyid",
        "/etc/portage/gnupg/trustdb.gpg",
    ):
        if fragment not in chroot:
            failures.append(f"getuto trust anchor contract is missing: {fragment}")

    for fragment in (
        "install -d -m 700 -o",
        "gpg --homedir /etc/portage/gnupg --keyserver",
        "--recv-keys",
    ):
        if fragment in chroot:
            failures.append(f"manual binpkg GPG setup bypasses getuto initialization: {fragment}")

    for fragment in (
        "role default を正とし",
        "official/local の compatible binary",
        "Python target migration と通常 runtime emerge より先",
    ):
        if fragment not in contract:
            failures.append(f"site input contract is missing: {fragment}")

    return failures


def source_texts() -> tuple[str, str, str, str, str]:
    return (
        read(DEFAULTS_PATH),
        read(CHROOT_PATH),
        read(PREPARE_PATH),
        read(MAKE_CONF_PATH),
        read(CONTRACT_PATH),
    )


def require_failure(label: str, texts: tuple[str, str, str, str, str]) -> None:
    if not validate(*texts):
        raise AssertionError(f"negative fixture unexpectedly passed: {label}")


def self_test(texts: tuple[str, str, str, str, str]) -> None:
    defaults, chroot, prepare, make_conf, contract = texts

    versioned = defaults.replace("  - net-libs/nodejs\n", "  - =net-libs/nodejs-26.3.0\n", 1)
    require_failure("versioned atom", (versioned, chroot, prepare, make_conf, contract))

    source_fallback = defaults.replace(
        'openwrt_gentoo_heavy_prebuilt_emerge_args: "--getbinpkgonly',
        'openwrt_gentoo_heavy_prebuilt_emerge_args: "--usepkgonly',
        1,
    )
    require_failure(
        "local-only option", (source_fallback, chroot, prepare, make_conf, contract)
    )

    heavy_name = "heavy prebuilt パッケージを configured binpkg repositories から事前導入"
    python_name = "Gentoo Portage を profile default Python 対応へ先行移行"
    reversed_order = chroot.replace(heavy_name, "__ORDER_SENTINEL__", 1)
    reversed_order = reversed_order.replace(python_name, heavy_name, 1)
    reversed_order = reversed_order.replace("__ORDER_SENTINEL__", python_name, 1)
    require_failure(
        "reversed preseed order", (defaults, reversed_order, prepare, make_conf, contract)
    )

    missing_getuto = chroot.replace(
        "/usr/bin/getuto",
        "/bin/true",
        1,
    )
    require_failure(
        "missing getuto trust helper",
        (defaults, missing_getuto, prepare, make_conf, contract),
    )

    precreated_gpg_home = chroot + "\ninstall -d -m 700 -o portage /etc/portage/gnupg\n"
    require_failure(
        "precreated empty GPG home",
        (defaults, precreated_gpg_home, prepare, make_conf, contract),
    )

    manual_recv_key = chroot + "\ngpg --recv-keys 2C44695DB9F6043D\n"
    require_failure(
        "manual GPG key receive",
        (defaults, manual_recv_key, prepare, make_conf, contract),
    )

    overridden_drop_user = make_conf + '\nGPG_VERIFY_USER_DROP="portage"\n'
    require_failure(
        "overridden GPG verification user",
        (defaults, chroot, prepare, overridden_drop_user, contract),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    texts = source_texts()
    failures = validate(*texts)
    if failures:
        for failure in failures:
            print(f"binary preseed contract failed: {failure}", file=sys.stderr)
        return 1
    if args.self_test:
        self_test(texts)
    print("openwrt Gentoo binary preseed contract ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
