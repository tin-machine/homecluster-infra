#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_PATH = (
    REPO_ROOT / "ansible/openwrt/playbooks/openwrt-srv-ext4-preflight.yml"
)
STORAGE_DEFAULTS_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_storage/defaults/main.yml"
STORAGE_TASKS_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_storage/tasks/main.yml"
STORAGE_PACKAGE_PREP_PATH = REPO_ROOT / "ansible/openwrt/roles/openwrt_storage/tasks/package_prep.yml"
PACKAGE_PREP_PLAYBOOK_PATH = (
    REPO_ROOT / "ansible/openwrt/playbooks/openwrt-srv-ext4-package-prep.yml"
)
INITIAL_BACKUP_PLAYBOOK_PATH = (
    REPO_ROOT / "ansible/openwrt/playbooks/openwrt-srv-ext4-initial-backup.yml"
)

FORMAT_READY_REQUIRED = {
    "source_mounted": "true",
    "source_readonly": "true",
    "required_commands_missing": "none",
    "rsync_acl_xattrs": "true",
    "backup_path_exists": "true",
    "backup_under_source": "false",
    "backup_same_device": "false",
    "backup_byte_capacity": "true",
    "backup_inode_capacity": "true",
    "blocking_writer_processes": "none",
    "dnsmasq_running": "true",
    "iscsi_target_running": "false",
    "detected_tcp_clients": "0",
    "iscsi_sessions": "0",
    "entry_scan_errors": "0",
    "nested_mountpoints": "0",
    "same_device_nested_mountpoints": "0",
}

COPY_READY_REQUIRED = {
    "source_mounted": "true",
    "required_commands_missing": "none",
    "rsync_acl_xattrs": "true",
    "backup_path_exists": "true",
    "backup_under_source": "false",
    "backup_same_device": "false",
    "backup_byte_capacity": "true",
    "backup_inode_capacity": "true",
    "entry_scan_errors": "0",
    "nested_mountpoints": "0",
    "same_device_nested_mountpoints": "0",
}


def parse_receipt(lines: list[str]) -> dict[str, str]:
    return dict(line.split("=", 1) for line in lines if "=" in line)


def format_ready_passes(lines: list[str]) -> bool:
    receipt = parse_receipt(lines)
    if any(receipt.get(key) != value for key, value in FORMAT_READY_REQUIRED.items()):
        return False
    for key in ("source_inodes_used", "backup_inodes_available"):
        if not re.fullmatch(r"[0-9]+", receipt.get(key, "")):
            return False
    return bool(re.fullmatch(r"[1-9][0-9]*", receipt.get("entry_count", "")))


def copy_ready_passes(lines: list[str]) -> bool:
    receipt = parse_receipt(lines)
    if any(receipt.get(key) != value for key, value in COPY_READY_REQUIRED.items()):
        return False
    for key in ("source_inodes_used", "backup_inodes_available"):
        if not re.fullmatch(r"[0-9]+", receipt.get(key, "")):
            return False
    return bool(re.fullmatch(r"[1-9][0-9]*", receipt.get("entry_count", "")))


def valid_format_ready_receipt() -> list[str]:
    return [
        "source_mounted=true",
        "source_readonly=true",
        "required_commands_missing=none",
        "rsync_acl_xattrs=true",
        "backup_path_exists=true",
        "backup_under_source=false",
        "backup_same_device=false",
        "backup_byte_capacity=true",
        "backup_inode_capacity=true",
        "source_inodes_used=100",
        "backup_inodes_available=1000",
        "writer_processes=dnsmasq",
        "blocking_writer_processes=none",
        "dnsmasq_running=true",
        "iscsi_target_running=false",
        "detected_tcp_clients=0",
        "iscsi_sessions=0",
        "entry_count=42",
        "entry_scan_errors=0",
        "nested_mountpoints=0",
        "same_device_nested_mountpoints=0",
    ]


def test_format_ready_positive_fixture() -> None:
    assert format_ready_passes(valid_format_ready_receipt())


def test_copy_ready_positive_fixture() -> None:
    assert copy_ready_passes(valid_format_ready_receipt())


def test_format_ready_negative_fixtures() -> None:
    for replacement in (
        "source_readonly=false",
        "required_commands_missing=tune2fs",
        "backup_same_device=true",
        "entry_scan_errors=1",
        "source_inodes_used=unknown",
        "blocking_writer_processes=nfsd",
        "dnsmasq_running=false",
        "iscsi_target_running=true",
    ):
        receipt = valid_format_ready_receipt()
        key = replacement.partition("=")[0] + "="
        receipt = [line for line in receipt if not line.startswith(key)]
        receipt.append(replacement)
        assert not format_ready_passes(receipt), replacement


def test_copy_ready_negative_fixtures() -> None:
    for replacement in (
        "backup_same_device=true",
        "entry_scan_errors=1",
        "nested_mountpoints=1",
        "source_inodes_used=unknown",
    ):
        receipt = valid_format_ready_receipt()
        key = replacement.partition("=")[0] + "="
        receipt = [line for line in receipt if not line.startswith(key)]
        receipt.append(replacement)
        assert not copy_ready_passes(receipt), replacement


def test_playbook_read_only_contract() -> None:
    content = PLAYBOOK_PATH.read_text(encoding="utf-8")
    required_fragments = (
        "openwrt_srv_ext4_preflight_mode: baseline",
        "['baseline', 'copy_ready', 'format_ready']",
        "openwrt_srv_ext4_preflight_backup_root",
        "OpenWrt /srv ext4 preflightをread-onlyで収集",
        "OpenWrt /srv ext4 format_ready hard gateを検証",
        "OpenWrt /srv ext4 copy_ready hard gateを検証",
        "source_readonly=true",
        "backup_same_device=false",
        "same_device_nested_mountpoints=0",
        "nested_mountpoints=0",
        "iscsi_sessions=0",
        "blocking_writer_processes=none",
        "dnsmasq_running=true",
        "iscsi_target_running=false",
        'if tcp_client_output="$(ss -Htn state established 2>/dev/null)"; then',
        "statvfs = os.statvfs(sys.argv[1])",
        "print(f\"{statvfs.f_files - statvfs.f_ffree}|{statvfs.f_favail}\")",
        "tgtadm --mode conn --op show",
    )
    for fragment in required_fragments:
        assert fragment in content, fragment
    assert "df -Pi" not in content
    assert "df -i" not in content
    assert "iscsiadm" not in content
    assert "dnsmasq stop" not in content

    disallowed_patterns = (
        r"ansible\.builtin\.(?:file|package|mount|service|systemd):",
        r"/etc/init\.d/",
        r"(?m)^\s*(?:sudo\s+)?(?:mkdir|umount)\b",
        r"(?m)^\s*(?:sudo\s+)?mkfs(?:\.[A-Za-z0-9_-]+)?\s",
        r"(?m)^\s*rsync\s+-a",
    )
    for pattern in disallowed_patterns:
        assert not re.search(pattern, content), pattern

    assert content.count("changed_when: false") >= 4


def test_ext4_preflight_packages_are_declared() -> None:
    content = STORAGE_DEFAULTS_PATH.read_text(encoding="utf-8")
    for package_name in (
        "e2fsprogs",
        "tune2fs",
        "dumpe2fs",
        "fstrim",
        "kmod-fs-ext4",
    ):
        assert re.search(rf"(?m)^  - {re.escape(package_name)}$", content), package_name


def test_ext4_format_uses_partition_scoped_options() -> None:
    storage_tasks = STORAGE_TASKS_PATH.read_text(encoding="utf-8")
    storage_readme = (STORAGE_TASKS_PATH.parent.parent / "README.md").read_text(encoding="utf-8")

    required_task_fragments = (
        "item.fstype in ['vfat', 'fat32', 'f2fs', 'ext4', 'swap']",
        "item.mkfs_options is not defined or (item.mkfs_options is sequence and item.mkfs_options is not string)",
        "ext4)",
        "/usr/sbin/mkfs.ext4 -F -L \"$label\"{% for option in item.mkfs_options | default([]) %} {{ option | quote }}{% endfor %} \"$part\"",
        "- item.format | default(true) | bool",
    )
    for fragment in required_task_fragments:
        assert fragment in storage_tasks, fragment

    required_readme_fragments = (
        "mkfs_options:",
        "- -b",
        "- '4096'",
        "- -I",
        "- '256'",
        "- -m",
        "- '1'",
        "- -N",
        "- '67108864'",
        "lazy_itable_init=0,lazy_journal_init=0",
        "format: false",
    )
    for fragment in required_readme_fragments:
        assert fragment in storage_readme, fragment


def test_ext4_package_prep_is_isolated_from_storage_mutation() -> None:
    storage_tasks = STORAGE_TASKS_PATH.read_text(encoding="utf-8")
    package_prep = STORAGE_PACKAGE_PREP_PATH.read_text(encoding="utf-8")
    playbook = PACKAGE_PREP_PLAYBOOK_PATH.read_text(encoding="utf-8")

    assert "ansible.builtin.import_tasks: package_prep.yml" in storage_tasks
    assert 'openwrt_package_names: "{{ openwrt_storage_packages }}"' in package_prep
    assert "openwrt_storage" in playbook
    assert "tasks_from: package_prep.yml" in playbook
    assert "openwrt_storage_force_format" not in playbook
    assert "openwrt_storage_manage_fstab" not in playbook
    assert "openwrt_storage_apply_mounts" not in playbook


def test_initial_backup_is_scoped_to_a_marked_data_directory() -> None:
    content = INITIAL_BACKUP_PLAYBOOK_PATH.read_text(encoding="utf-8")

    required_fragments = (
        "openwrt_srv_ext4_backup_source_path: /srv",
        'openwrt_srv_ext4_backup_root: ""',
        "openwrt_srv_ext4_initial_backup_confirm_expected: initial-backup-20260806",
        "openwrt_srv_ext4_backup_id is match('^[A-Za-z0-9][A-Za-z0-9._-]*$')",
        "backup_same_device",
        "nested_mountpoints_present",
        ".homecluster-ext4-initial-backup",
        "format=rsync-aHAXSx-numeric-ids",
        "- --no-specials",
        "- --delete",
        '- "{{ openwrt_srv_ext4_backup_data_path }}/"',
    )
    for fragment in required_fragments:
        assert fragment in content, fragment

    assert ("backup" + "-disk") not in content

    forbidden_fragments = (
        "mkfs.ext4",
        "umount ",
        "block mount",
        "/etc/init.d/",
        '- "{{ openwrt_srv_ext4_backup_root }}/"',
    )
    for fragment in forbidden_fragments:
        assert fragment not in content, fragment


def main() -> None:
    test_format_ready_positive_fixture()
    test_copy_ready_positive_fixture()
    test_format_ready_negative_fixtures()
    test_copy_ready_negative_fixtures()
    test_playbook_read_only_contract()
    test_ext4_preflight_packages_are_declared()
    test_ext4_format_uses_partition_scoped_options()
    test_ext4_package_prep_is_isolated_from_storage_mutation()
    test_initial_backup_is_scoped_to_a_marked_data_directory()
    print("openwrt /srv ext4 preflight checks ok")


if __name__ == "__main__":
    main()
