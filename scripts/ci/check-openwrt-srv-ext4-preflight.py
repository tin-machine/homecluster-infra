#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_PATH = (
    REPO_ROOT / "ansible/openwrt/playbooks/openwrt-srv-ext4-preflight.yml"
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


def test_playbook_read_only_contract() -> None:
    content = PLAYBOOK_PATH.read_text(encoding="utf-8")
    required_fragments = (
        "openwrt_srv_ext4_preflight_mode: baseline",
        "openwrt_srv_ext4_preflight_backup_root",
        "OpenWrt /srv ext4 preflightをread-onlyで収集",
        "OpenWrt /srv ext4 format_ready hard gateを検証",
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


def main() -> None:
    test_format_ready_positive_fixture()
    test_format_ready_negative_fixtures()
    test_playbook_read_only_contract()
    print("openwrt /srv ext4 preflight checks ok")


if __name__ == "__main__":
    main()
