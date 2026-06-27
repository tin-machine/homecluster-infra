#!/usr/bin/env python3
"""Check source-only consistency between OpenWrt sysupgrade manifests and post-upgrade collector."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


MANIFEST_PATH = Path("ansible/openwrt/roles/openwrt_sysupgrade/tasks/manifest.yml")
SYSUPGRADE_MAIN_PATH = Path("ansible/openwrt/roles/openwrt_sysupgrade/tasks/main.yml")
SYSUPGRADE_RESET_CONNECTION_PATH = Path("ansible/openwrt/roles/openwrt_sysupgrade/tasks/reset_connection.yml")
COLLECTOR_PATH = Path(".agents/skills/homecluster-openwrt-postupgrade-check/scripts/collect-openwrt-postupgrade.sh")
SCHEMA_PATH = Path(".agents/skills/homecluster-openwrt-postupgrade-check/references/postupgrade-output-schema.md")

EXPECTED_SERVICE_LIST = [
    "network",
    "firewall",
    "dnsmasq",
    "rpcbind",
    "nfsd",
    "frr",
    "banip",
    "prometheus-node-exporter-lua",
    "uhttpd",
    "log",
]

REQUIRED_SCHEMA_PATTERNS = [
    "router.package_manager.commands",
    "router.package_manager.selected_for_packages",
    "missing_required_packages",
    "missing_required_services",
    "expected_mount_missing",
    "route_target_rejected_dev",
    "bgp_unavailable",
    "k3s_unreachable",
    "ssh_host_key_problem",
]

REQUIRED_COLLECTOR_KEYS = [
    "selected_for_packages",
    "installed_count",
    "missing_required",
    "not_ready",
    "expected_missing",
    "route_target_rejected_dev",
    "missing_required_packages",
    "missing_required_services",
    "expected_mount_missing",
    "k3s_unreachable",
]


def repo_root() -> Path:
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def read_text(root: Path, path: Path) -> str:
    return (root / path).read_text(encoding="utf-8")


def load_yaml(root: Path, path: Path) -> Any:
    return yaml.safe_load(read_text(root, path))


def iter_tasks(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from iter_tasks(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("block", "rescue", "always"):
        if key in node:
            yield from iter_tasks(node[key])


def task_raw_text(task: dict[str, Any]) -> str:
    value = task.get("ansible.builtin.raw", task.get("raw", ""))
    return value if isinstance(value, str) else ""


def manifest_raw_blocks(root: Path) -> list[str]:
    data = load_yaml(root, MANIFEST_PATH)
    return [task_raw_text(task) for task in iter_tasks(data) if task_raw_text(task)]


def service_list_from_text(text: str) -> list[str]:
    match = re.search(r"for\s+s\s+in\s+([^;\n]+);\s+do", text)
    if not match:
        return []
    return match.group(1).split()


def require_contains(findings: list[dict[str, str]], path: Path, text: str, pattern: str, issue: str) -> None:
    if pattern not in text:
        findings.append({"path": str(path), "issue": issue, "detail": pattern})


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def in_run_router_single_quote(text: str, offset: int) -> bool:
    marker = "run_router '"
    start = text.rfind(marker, 0, offset)
    if start < 0:
        return False
    quote_start = start + len(marker)
    quote_end = text.find("')", quote_start)
    return quote_end != -1 and quote_start <= offset < quote_end


def check_package_manager_contract(root: Path, findings: list[dict[str, str]]) -> None:
    manifest_text = read_text(root, MANIFEST_PATH)
    collector_text = read_text(root, COLLECTOR_PATH)
    manifest_blocks = "\n".join(manifest_raw_blocks(root))

    required_selection_fragments = [
        'major="${release%%.*}"',
        "command -v apk",
        "command -v opkg",
        'if [ "$major" -ge 25 ] && [ "$have_apk" -eq 1 ]; then',
        'elif [ "$major" -gt 0 ] && [ "$major" -lt 25 ] && [ "$have_opkg" -eq 1 ]; then',
        'printf "apk\\n"',
        'printf "opkg\\n"',
    ]
    for fragment in required_selection_fragments:
        require_contains(findings, MANIFEST_PATH, manifest_blocks, fragment, "manifest-package-manager-selection-missing")
        require_contains(findings, COLLECTOR_PATH, collector_text, fragment, "collector-package-manager-selection-missing")

    for fragment in ("apk info", 'opkg list-installed | cut -d" " -f1'):
        require_contains(findings, MANIFEST_PATH, manifest_text, fragment, "manifest-package-list-command-missing")
        require_contains(findings, COLLECTOR_PATH, collector_text, fragment, "collector-package-list-command-missing")

    for match in re.finditer(r"command\s+-v\s+(apk|opkg)\b", collector_text):
        if not in_run_router_single_quote(collector_text, match.start()):
            findings.append(
                {
                    "path": str(COLLECTOR_PATH),
                    "line": str(line_number(collector_text, match.start())),
                    "issue": "collector-local-package-manager-probe",
                    "detail": match.group(0),
                }
            )


def check_service_contract(root: Path, findings: list[dict[str, str]]) -> None:
    manifest_services = service_list_from_text(read_text(root, MANIFEST_PATH))
    collector_services = service_list_from_text(read_text(root, COLLECTOR_PATH))
    if manifest_services != EXPECTED_SERVICE_LIST:
        findings.append(
            {
                "path": str(MANIFEST_PATH),
                "issue": "manifest-service-list-mismatch",
                "detail": f"expected={EXPECTED_SERVICE_LIST!r} actual={manifest_services!r}",
            }
        )
    if collector_services != EXPECTED_SERVICE_LIST:
        findings.append(
            {
                "path": str(COLLECTOR_PATH),
                "issue": "collector-service-list-mismatch",
                "detail": f"expected={EXPECTED_SERVICE_LIST!r} actual={collector_services!r}",
            }
        )


def first_line_containing(text: str, needle: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return index
    return 0


def check_manifest_order(root: Path, findings: list[dict[str, str]]) -> None:
    main_text = read_text(root, SYSUPGRADE_MAIN_PATH)
    reset_text = read_text(root, SYSUPGRADE_RESET_CONNECTION_PATH)
    required = {
        "verify": "ansible.builtin.import_tasks: verify.yml",
        "pre_manifest": "owrt_manifest_phase: pre",
        "backup": "ansible.builtin.import_tasks: backup.yml",
        "upgrade": "ansible.builtin.import_tasks: upgrade.yml",
        "reset": "ansible.builtin.include_tasks: reset_connection.yml",
        "post_manifest": "owrt_manifest_phase: post",
    }
    lines = {name: first_line_containing(main_text, needle) for name, needle in required.items()}
    missing = [name for name, line in lines.items() if line == 0]
    if missing:
        findings.append({"path": str(SYSUPGRADE_MAIN_PATH), "issue": "manifest-order-marker-missing", "detail": ", ".join(missing)})
        return
    if not (lines["verify"] < lines["pre_manifest"] < lines["backup"]):
        findings.append(
            {
                "path": str(SYSUPGRADE_MAIN_PATH),
                "issue": "pre-manifest-order-invalid",
                "detail": json.dumps(lines, sort_keys=True),
            }
        )
    if not (lines["upgrade"] < lines["reset"] < lines["post_manifest"]):
        findings.append(
            {
                "path": str(SYSUPGRADE_MAIN_PATH),
                "issue": "post-manifest-order-invalid",
                "detail": json.dumps(lines, sort_keys=True),
            }
        )
    require_contains(
        findings,
        SYSUPGRADE_RESET_CONNECTION_PATH,
        reset_text,
        "ansible.builtin.meta: reset_connection",
        "reset-connection-task-missing",
    )
    if "when:" in reset_text:
        findings.append(
            {
                "path": str(SYSUPGRADE_RESET_CONNECTION_PATH),
                "issue": "reset-connection-task-has-unsupported-when",
                "detail": "meta reset_connection must be conditionally included, not guarded directly",
            }
        )


def check_collector_schema_contract(root: Path, findings: list[dict[str, str]]) -> None:
    collector_text = read_text(root, COLLECTOR_PATH)
    schema_text = read_text(root, SCHEMA_PATH)
    for key in REQUIRED_COLLECTOR_KEYS:
        require_contains(findings, COLLECTOR_PATH, collector_text, key, "collector-output-key-missing")
    for pattern in REQUIRED_SCHEMA_PATTERNS:
        require_contains(findings, SCHEMA_PATH, schema_text, pattern, "schema-pattern-missing")

    if "release_raw" in collector_text:
        for pattern in (
            r'echo\s+"\$release_raw"\s*\|',
            r'\$release_raw\s*\|\s*grep',
            r'\$release_raw\s*\|\s*cut',
        ):
            for match in re.finditer(pattern, collector_text):
                findings.append(
                    {
                        "path": str(COLLECTOR_PATH),
                        "line": str(line_number(collector_text, match.start())),
                        "issue": "collector-capture-json-parsed-as-plain-text",
                        "detail": match.group(0),
                    }
                )


def run_check(root: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in (MANIFEST_PATH, SYSUPGRADE_MAIN_PATH, SYSUPGRADE_RESET_CONNECTION_PATH, COLLECTOR_PATH, SCHEMA_PATH):
        if not (root / path).is_file():
            findings.append({"path": str(path), "issue": "required-file-missing", "detail": ""})
    if findings:
        return {"ok": False, "findings": findings, "summary": "required source file missing"}

    check_package_manager_contract(root, findings)
    check_service_contract(root, findings)
    check_manifest_order(root, findings)
    check_collector_schema_contract(root, findings)

    return {
        "ok": not findings,
        "findings": findings,
        "service_list": EXPECTED_SERVICE_LIST,
        "summary": "post-upgrade source contract is clean" if not findings else "post-upgrade source contract regression found",
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_contract_fixture(root: Path) -> None:
    write(
        root / MANIFEST_PATH,
        """---
- name: manifest manager
  ansible.builtin.raw: |
    release=""
    major="${release%%.*}"
    command -v apk >/dev/null 2>&1 && have_apk=1
    command -v opkg >/dev/null 2>&1 && have_opkg=1
    if [ "$major" -ge 25 ] && [ "$have_apk" -eq 1 ]; then
      printf "apk\\n"
    elif [ "$major" -gt 0 ] && [ "$major" -lt 25 ] && [ "$have_opkg" -eq 1 ]; then
      printf "opkg\\n"
    else
      printf "\\n"
    fi
- name: manifest packages
  ansible.builtin.raw: |
    apk info
    opkg list-installed | cut -d" " -f1
- name: manifest services
  ansible.builtin.raw: |
    for s in network firewall dnsmasq rpcbind nfsd frr banip prometheus-node-exporter-lua uhttpd log; do
      printf "SERVICE %s " "$s"
    done
""",
    )
    write(
        root / SYSUPGRADE_MAIN_PATH,
        """---
- ansible.builtin.import_tasks: verify.yml
- vars:
    owrt_manifest_phase: pre
  ansible.builtin.import_tasks: manifest.yml
- ansible.builtin.import_tasks: backup.yml
- ansible.builtin.import_tasks: upgrade.yml
- ansible.builtin.include_tasks: reset_connection.yml
- vars:
    owrt_manifest_phase: post
  ansible.builtin.import_tasks: manifest.yml
""",
    )
    write(
        root / SYSUPGRADE_RESET_CONNECTION_PATH,
        """---
- ansible.builtin.meta: reset_connection
""",
    )
    write(
        root / COLLECTOR_PATH,
        """#!/usr/bin/env bash
pkg_mgr_raw="$(run_router 'for c in opkg apk; do command -v "$c" >/dev/null 2>&1 && echo "$c"; done; true')"
selected_pkg_manager_raw="$(run_router '
release=""
major="${release%%.*}"
command -v apk >/dev/null 2>&1 && have_apk=1
command -v opkg >/dev/null 2>&1 && have_opkg=1
if [ "$major" -ge 25 ] && [ "$have_apk" -eq 1 ]; then
  printf "apk\\n"
elif [ "$major" -gt 0 ] && [ "$major" -lt 25 ] && [ "$have_opkg" -eq 1 ]; then
  printf "opkg\\n"
else
  printf "\\n"
fi
')"
apk info
opkg list-installed | cut -d" " -f1
services_raw="$(run_router 'for s in network firewall dnsmasq rpcbind nfsd frr banip prometheus-node-exporter-lua uhttpd log; do printf "SERVICE %s " "$s"; done')"
selected_for_packages installed_count missing_required not_ready expected_missing route_target_rejected_dev missing_required_packages missing_required_services expected_mount_missing k3s_unreachable
""",
    )
    write(root / SCHEMA_PATH, "\n".join(REQUIRED_SCHEMA_PATTERNS))


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        copy_contract_fixture(root)
        good = run_check(root)
        if not good["ok"]:
            print(json.dumps(good, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        text = read_text(root, COLLECTOR_PATH).replace("prometheus-node-exporter-lua ", "")
        write(root / COLLECTOR_PATH, text)
        bad = run_check(root)
        if bad["ok"] or not any(item["issue"] == "collector-service-list-mismatch" for item in bad["findings"]):
            print(json.dumps(bad, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", help="repository root to audit; defaults to current git root")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve() if args.root else repo_root()
    result = run_check(root)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
