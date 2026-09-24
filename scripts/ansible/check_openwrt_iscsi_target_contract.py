#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROLE_DIR = Path("ansible/openwrt/roles/openwrt_iscsi_target")
SITE_FILE = Path("ansible/openwrt/site.yml")


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
        template = read(ROLE_DIR / "templates/tgtd.conf.j2")
        handlers = read(ROLE_DIR / "handlers/main.yml")
        site = read(SITE_FILE)
    except FileNotFoundError as exc:
        print(
            json.dumps(
                {"ok": False, "findings": [f"missing file: {exc}"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    require(
        re.search(r"^openwrt_iscsi_target_enabled:\s*false$", defaults, re.M) is not None,
        findings,
        "openwrt_iscsi_target_enabled must default to false",
    )
    require(
        re.search(r"^openwrt_iscsi_targets:\s*\[\]$", defaults, re.M) is not None,
        findings,
        "openwrt_iscsi_targets must default to []",
    )
    require(
        "openwrt_iscsi_target_preallocate:" in defaults
        and "openwrt_iscsi_target_allow_sparse:" in defaults,
        findings,
        "backing file allocation policy defaults are missing",
    )
    require(
        "ansible.builtin.opkg:" not in tasks and "name: openwrt_package" in tasks,
        findings,
        "iSCSI target role must use openwrt_package instead of direct opkg",
    )
    require(
        "rm -f" not in tasks,
        findings,
        "iSCSI target role must not delete existing backing files",
    )
    for field in ("target_iqn", "backing_file", "initiator_iqn", "client_ip", "size_gib"):
        require(field in tasks, findings, f"required target validation field is missing: {field}")
    require(
        "existing backing file has unexpected size" in tasks,
        findings,
        "existing backing file size mismatch must fail closed",
    )
    require(
        "wc -c" not in tasks and "os.path.getsize" in tasks,
        findings,
        "existing backing file size check must not read the full file",
    )
    require(
        "allow_address" in template
        and "allow_name" in template
        and "option device" in template,
        findings,
        "tgt template must constrain address/name and render LUN device",
    )
    require(
        "Restart tgtd" in handlers and "changed_when: true" in handlers,
        findings,
        "tgtd handler must exist and mark restart as changed",
    )
    require(
        re.search(
            r"- role: openwrt_iscsi_target\s+tags:\s+\['never', 'iscsi_target'\]",
            site,
            re.S,
        )
        is not None,
        findings,
        "site.yml must include openwrt_iscsi_target behind never/iscsi_target tags",
    )

    role_text = "\n".join([defaults, tasks, template, handlers])
    private_patterns = [
        r"\brpi[45]-\d+\b",
        r"\b10\.\d+\.\d+\.\d+\b",
        r"iqn\.2026-0[46]\.home",
    ]
    for pattern in private_patterns:
        require(
            re.search(pattern, role_text) is None,
            findings,
            f"private-looking value found in public iSCSI role: {pattern}",
        )

    result = {
        "ok": not findings,
        "findings": findings,
        "summary": "openwrt_iscsi_target contract is clean" if not findings else "openwrt_iscsi_target contract failed",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
