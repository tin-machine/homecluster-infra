#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROLE_DIR = Path("ansible/arm64/roles/k3s_local_storage")


def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def require(condition: bool, findings: list[str], message: str) -> None:
    if not condition:
        findings.append(message)


def main() -> int:
    findings: list[str] = []

    try:
        defaults = read(ROLE_DIR / "defaults/main.yml")
        tasks = read(ROLE_DIR / "tasks/main.yml")
        mount_dropin = read(ROLE_DIR / "templates/k3s-requires-data-mount.conf.j2")
        password_dropin = read(ROLE_DIR / "templates/k3s-node-password-sync.conf.j2")
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "findings": [f"missing file: {exc}"]}, ensure_ascii=False, sort_keys=True))
        return 1

    require(
        "k3s_local_storage_mountpoint: /var/lib/rancher/k3s" in defaults,
        findings,
        "k3s local storage mountpoint must default to /var/lib/rancher/k3s",
    )
    require(
        "k3s_local_storage_mount_dependency_dropin_name:" in defaults,
        findings,
        "k3s mount dependency drop-in name default is missing",
    )
    require(
        "k3s-requires-data-mount.conf.j2" in tasks
        and "k3s_local_storage_mount_dependency_dropin_name" in tasks,
        findings,
        "k3s local storage role must render the mount dependency drop-in",
    )
    require(
        "RequiresMountsFor={{ k3s_local_storage_mountpoint }}" in mount_dropin,
        findings,
        "k3s mount dependency drop-in must use RequiresMountsFor for the configured mountpoint",
    )
    require(
        "ConditionPathIsMountPoint={{ k3s_local_storage_mountpoint }}" in mount_dropin,
        findings,
        "k3s mount dependency drop-in must prevent k3s from starting on the root overlay before the data-dir mount exists",
    )
    require(
        "[Unit]" in mount_dropin and "[Service]" not in mount_dropin,
        findings,
        "k3s mount dependency belongs in the systemd [Unit] section",
    )
    require(
        "k3s service drop-in ディレクトリを作成" in tasks
        and "when: k3s_local_storage_enabled | bool" in tasks,
        findings,
        "k3s service drop-in directory must be created whenever k3s_local_storage is enabled",
    )
    require(
        "ExecStartPre={{ k3s_local_storage_node_password_sync_script_path }} restore" in password_dropin,
        findings,
        "node identity restore must remain an ExecStartPre hook",
    )
    require(
        "ExecStartPost={{ k3s_local_storage_node_password_sync_script_path }} persist" in password_dropin,
        findings,
        "node identity persist must remain an ExecStartPost hook",
    )

    result = {
        "ok": not findings,
        "findings": findings,
        "summary": "k3s_local_storage contract is clean" if not findings else "k3s_local_storage contract failed",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
