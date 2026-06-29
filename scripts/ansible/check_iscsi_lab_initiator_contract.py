#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


ROLE_DIR = Path("ansible/arm64/roles/iscsi_lab_initiator")
SITE_FILE = Path("ansible/arm64/site.yml")


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
        site = read(SITE_FILE)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "findings": [f"missing file: {exc}"]}, ensure_ascii=False, sort_keys=True))
        return 1

    require(
        re.search(r"^iscsi_lab_enabled:\s*false$", defaults, re.M) is not None,
        findings,
        "iscsi_lab_enabled must default to false",
    )
    require(
        "iscsi_lab_initiator_enabled: \"{{ iscsi_lab_enabled | default(false) }}\"" in defaults,
        findings,
        "iscsi_lab_initiator_enabled must follow the inventory contract key",
    )
    require(
        '\\"' not in tasks,
        findings,
        "initiator task YAML must not contain literal backslash-quoted values",
    )
    require(
        "package: \"{{ iscsi_lab_open_iscsi_package }}\"" in tasks,
        findings,
        "open-iscsi package must be passed without embedded quote characters",
    )
    require(
        "already present" in tasks and "iscsi_lab_login.rc == 15" in tasks,
        findings,
        "iscsiadm login must treat an existing session as idempotent",
    )
    for guard in (
        "iscsi_lab_login_enabled | bool",
        "iscsi_lab_allow_format | bool",
        "iscsi_lab_mount_enabled | bool",
        "iscsi_lab_mountpoint != '/var/lib/rancher/k3s'",
    ):
        require(guard in tasks, findings, f"required initiator safety guard is missing: {guard}")
    require(
        re.search(r"- role: iscsi_lab_initiator", site) is not None
        and "- never" in site
        and "- iscsi_lab" in site,
        findings,
        "site.yml must include iscsi_lab_initiator behind never/iscsi_lab tags",
    )

    result = {
        "ok": not findings,
        "findings": findings,
        "summary": "iscsi_lab_initiator contract is clean" if not findings else "iscsi_lab_initiator contract failed",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
