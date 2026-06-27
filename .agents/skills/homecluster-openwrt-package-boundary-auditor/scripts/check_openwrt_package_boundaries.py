#!/usr/bin/env python3
"""Audit OpenWrt package-manager source boundaries."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


ALLOWED_BOUNDARY_ROLES = {
    "bootstrap_python",
    "openwrt_detect",
    "openwrt_package",
    "openwrt_sysupgrade",
}

MANAGER_TOKEN_RE = re.compile(r"\b(?:opkg|apk)\b")
DIRECT_MODULE_RE = re.compile(r"^\s*(?:ansible\.builtin\.)?opkg\s*:")
COMMAND_MODULE_RE = re.compile(r"^(?P<indent>\s*)(?:ansible\.builtin\.)?(?P<module>raw|shell|command)\s*:\s*(?P<value>.*)$")
TASKS_GLOB = "ansible/openwrt/roles/*/tasks/**/*.yml"
TASKS_GLOB_ALT = "ansible/openwrt/roles/*/tasks/**/*.yaml"


def repo_root() -> Path:
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def role_from_path(path: Path) -> str:
    parts = path.parts
    try:
        roles_index = parts.index("roles")
    except ValueError:
        return ""
    if roles_index + 1 >= len(parts):
        return ""
    return parts[roles_index + 1]


def line_context(lines: list[str], index: int) -> str:
    return lines[index].strip()


def collect_block_scalar(lines: list[str], start_index: int, base_indent: int) -> str:
    collected: list[str] = []
    for line in lines[start_index + 1 :]:
        if not line.strip():
            collected.append(line)
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= base_indent:
            break
        collected.append(line)
    return "\n".join(collected)


def scan_direct_package_manager_calls(path: Path) -> list[dict[str, Any]]:
    role = role_from_path(path)
    if role in ALLOWED_BOUNDARY_ROLES:
        return []

    findings: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        line_no = index + 1
        if DIRECT_MODULE_RE.search(line):
            findings.append(
                {
                    "path": str(path),
                    "line": line_no,
                    "role": role,
                    "kind": "direct-opkg-module",
                    "snippet": line_context(lines, index),
                }
            )
            continue

        match = COMMAND_MODULE_RE.match(line)
        if not match:
            continue

        value = match.group("value").strip()
        module_name = match.group("module")
        base_indent = len(match.group("indent"))
        haystack = value
        if value in {"|", ">"} or value.startswith("|") or value.startswith(">"):
            haystack = collect_block_scalar(lines, index, base_indent)

        if MANAGER_TOKEN_RE.search(haystack):
            findings.append(
                {
                    "path": str(path),
                    "line": line_no,
                    "role": role,
                    "kind": f"direct-{module_name}-package-manager-call",
                    "snippet": line_context(lines, index),
                }
            )

    return findings


def load_yaml_file(path: Path) -> tuple[Any | None, list[dict[str, Any]]]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), []
    except yaml.YAMLError as exc:
        return None, [{"path": str(path), "issue": "yaml-parse-error", "detail": str(exc)}]


def iter_task_dicts(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from iter_task_dicts(item)
        return
    if not isinstance(node, dict):
        return

    if any(key in node for key in ("ansible.builtin.include_role", "include_role", "block", "rescue", "always")):
        yield node

    for key in ("block", "rescue", "always"):
        if key in node:
            yield from iter_task_dicts(node[key])


def include_role_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("name", ""))
    return ""


def check_openwrt_package_include(path: Path, task: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    module_key = ""
    module_value: Any = None
    for candidate in ("ansible.builtin.include_role", "include_role"):
        if candidate in task:
            module_key = candidate
            module_value = task[candidate]
            break
    if not module_key or include_role_name(module_value) != "openwrt_package":
        return []

    task_name = str(task.get("name", ""))
    role = role_from_path(path)

    def add(issue: str, detail: str = "") -> None:
        issues.append(
            {
                "path": str(path),
                "role": role,
                "task": task_name,
                "issue": issue,
                "detail": detail,
            }
        )

    if isinstance(module_value, dict):
        nested_bad_keys = sorted(k for k in module_value if k == "vars" or k.startswith("openwrt_package_"))
        if nested_bad_keys:
            add("openwrt-package-vars-nested-under-include-role", ", ".join(nested_bad_keys))

    task_vars = task.get("vars")
    if not isinstance(task_vars, dict):
        add("openwrt-package-task-vars-missing")
        return issues

    required_vars = {
        "openwrt_package_names",
        "openwrt_package_state",
        "openwrt_package_update_cache",
    }
    missing_vars = sorted(required_vars - set(task_vars))
    if missing_vars:
        add("openwrt-package-required-vars-missing", ", ".join(missing_vars))

    names_value = task_vars.get("openwrt_package_names")
    if isinstance(names_value, str) and "{{" not in names_value:
        add("openwrt-package-names-bare-string", names_value)
    elif not isinstance(names_value, (str, list, tuple)) and names_value is not None:
        add("openwrt-package-names-unsupported-type", type(names_value).__name__)

    state_value = task_vars.get("openwrt_package_state")
    if isinstance(state_value, str) and "{{" not in state_value and state_value not in {"present", "absent"}:
        add("openwrt-package-state-unsupported", state_value)

    for forbidden in ("loop", "loop_control"):
        if forbidden in task:
            add("openwrt-package-include-has-loop", forbidden)

    return issues


def scan_include_role_contracts(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data, parse_errors = load_yaml_file(path)
    if parse_errors:
        return [], parse_errors

    issues: list[dict[str, Any]] = []
    for task in iter_task_dicts(data):
        issues.extend(check_openwrt_package_include(path, task))
    return issues, []


def discover_task_files(root: Path) -> list[Path]:
    files = set(root.glob(TASKS_GLOB))
    files.update(root.glob(TASKS_GLOB_ALT))
    return sorted(path for path in files if path.is_file())


def run_audit(root: Path) -> dict[str, Any]:
    task_files = discover_task_files(root)
    direct_findings: list[dict[str, Any]] = []
    include_issues: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []

    for path in task_files:
        relative = str(path.relative_to(root))
        findings = scan_direct_package_manager_calls(path)
        issues, errors = scan_include_role_contracts(path)
        for item in [*findings, *issues, *errors]:
            item["path"] = relative
        direct_findings.extend(findings)
        include_issues.extend(issues)
        parse_errors.extend(errors)

    ok = not direct_findings and not include_issues and not parse_errors
    return {
        "ok": ok,
        "allowed_boundaries": sorted(ALLOWED_BOUNDARY_ROLES),
        "scanned_files": len(task_files),
        "unexpected_package_manager_calls": direct_findings,
        "include_role_issues": include_issues,
        "parse_errors": parse_errors,
        "summary": "package-manager boundaries are clean" if ok else "package-manager boundary regression found",
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write(
            root / "ansible/openwrt/roles/openwrt_package/tasks/main.yml",
            "- name: Boundary\n  ansible.builtin.opkg:\n    name: zlib\n",
        )
        write(
            root / "ansible/openwrt/roles/openwrt_nfs_server/tasks/main.yml",
            """---
- name: NFS packages
  ansible.builtin.include_role:
    name: openwrt_package
  vars:
    openwrt_package_names: "{{ openwrt_nfs_packages }}"
    openwrt_package_state: present
    openwrt_package_update_cache: "{{ openwrt_opkg_update | default(true) }}"
""",
        )
        clean = run_audit(root)
        if not clean["ok"]:
            print(json.dumps(clean, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        write(
            root / "ansible/openwrt/roles/openwrt_banip/tasks/main.yml",
            "- name: Bad package\n  ansible.builtin.raw: opkg install banip\n",
        )
        bad = run_audit(root)
        if bad["ok"] or not bad["unexpected_package_manager_calls"]:
            print(json.dumps(bad, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        write(
            root / "ansible/openwrt/roles/openwrt_frr/tasks/main.yml",
            """---
- name: Bad include
  ansible.builtin.include_role:
    name: openwrt_package
    openwrt_package_names: "{{ openwrt_frr_packages }}"
""",
        )
        bad_include = run_audit(root)
        if bad_include["ok"] or not bad_include["include_role_issues"]:
            print(json.dumps(bad_include, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", help="repository root to audit; defaults to current git root")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve() if args.root else repo_root()
    result = run_audit(root)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
