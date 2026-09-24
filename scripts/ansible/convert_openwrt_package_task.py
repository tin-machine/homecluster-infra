#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


CONSUMED_KEYS = {
    "ansible.builtin.opkg",
    "ansible.builtin.shell",
    "loop",
    "loop_control",
    "register",
    "changed_when",
    "failed_when",
}


@dataclass(frozen=True)
class TaskBlock:
    start: int
    end: int
    indent: str
    lines: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert one OpenWrt package task to include_role: openwrt_package."
    )
    parser.add_argument("--file", type=Path, help="Ansible task file to edit")
    parser.add_argument("--task-name", help="Exact task name to convert")
    parser.add_argument(
        "--package-expression",
        help="Package list expression. Bare variable names are rendered as Jinja.",
    )
    parser.add_argument("--state", choices=("present", "absent"), default="present")
    parser.add_argument(
        "--update-cache",
        help="Value for openwrt_package_update_cache. Defaults to opkg update for present and false for absent.",
    )
    parser.add_argument("--write", action="store_true", help="Write the converted file")
    parser.add_argument("--self-test", action="store_true", help="Run built-in converter tests")
    return parser.parse_args()


def find_task_block(lines: list[str], task_name: str) -> TaskBlock:
    pattern = re.compile(rf"^(\s*)- name: {re.escape(task_name)}\n?$")
    start = None
    indent = ""
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            start = index
            indent = match.group(1)
            break
    if start is None:
        raise SystemExit(f"task not found: {task_name}")

    end = len(lines)
    next_task = re.compile(rf"^{re.escape(indent)}- name: ")
    for index in range(start + 1, len(lines)):
        if next_task.match(lines[index]):
            end = index
            break
    return TaskBlock(start=start, end=end, indent=indent, lines=lines[start:end])


def top_level_key(line: str, indent: str) -> str | None:
    match = re.match(rf"^{re.escape(indent)}  ([A-Za-z0-9_.-]+):(?:\s|$)", line)
    return match.group(1) if match else None


def top_level_chunks(block: TaskBlock) -> list[tuple[str, list[str]]]:
    chunks: list[tuple[str, list[str]]] = []
    index = 1
    while index < len(block.lines):
        key = top_level_key(block.lines[index], block.indent)
        if key is None:
            index += 1
            continue

        end = len(block.lines)
        for probe in range(index + 1, len(block.lines)):
            if top_level_key(block.lines[probe], block.indent) is not None:
                end = probe
                break
        chunks.append((key, block.lines[index:end]))
        index = end
    return chunks


def derive_loop_expression(block: TaskBlock) -> str | None:
    for key, chunk in top_level_chunks(block):
        if key == "loop" and chunk:
            _, value = chunk[0].split(":", 1)
            return value.strip()
    return None


def derive_opkg_name_expression(block: TaskBlock) -> str | None:
    for key, chunk in top_level_chunks(block):
        if key != "ansible.builtin.opkg":
            continue
        name_pattern = re.compile(rf"^{re.escape(block.indent)}    name:\s*(.+?)\s*$")
        for line in chunk:
            match = name_pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if value in {'"{{ item }}"', "'{{ item }}'", "{{ item }}"}:
                return None
            if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                value = value[1:-1]
            if value.startswith("{{"):
                return value
            return f"['{value}']"
    return None


def derive_remove_expression(block: TaskBlock) -> str | None:
    text = "".join(block.lines)
    match = re.search(r"\bopkg\s+remove\s+([A-Za-z0-9_.+-]+)\b", text)
    if not match:
        return None
    return f"['{match.group(1)}']"


def normalize_package_expression(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise SystemExit("package expression is empty")
    if value.startswith(("'", '"')):
        return value
    if value.startswith("{{") and value.endswith("}}"):
        return f'"{value}"'
    if value.startswith("[") or value.startswith("{"):
        return value
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\s*\|.*)?$", value):
        return f'"{{{{ {value} }}}}"'
    return value


def default_update_cache(state: str) -> str:
    if state == "absent":
        return "false"
    return '"{{ openwrt_opkg_update | default(true) }}"'


def validate_supported_source(block: TaskBlock, state: str) -> None:
    keys = {key for key, _ in top_level_chunks(block)}
    if "ansible.builtin.opkg" in keys:
        if "loop" in keys or derive_opkg_name_expression(block):
            return
        raise SystemExit("opkg conversion requires a loop package list or literal package name")
    if state == "absent" and "ansible.builtin.shell" in keys and derive_remove_expression(block):
        return
    raise SystemExit("supported source task not found: expected opkg loop or raw opkg remove task")


def convert_block(block: TaskBlock, package_expression: str | None, state: str, update_cache: str | None) -> list[str]:
    validate_supported_source(block, state)

    if package_expression is None:
        package_expression = derive_loop_expression(block)
    if package_expression is None:
        package_expression = derive_opkg_name_expression(block)
    if package_expression is None and state == "absent":
        package_expression = derive_remove_expression(block)
    if package_expression is None:
        raise SystemExit("package expression could not be derived; pass --package-expression")

    preserved = [
        chunk
        for key, chunk in top_level_chunks(block)
        if key not in CONSUMED_KEYS
    ]

    indent = block.indent
    converted = [
        block.lines[0],
        f"{indent}  ansible.builtin.include_role:\n",
        f"{indent}    name: openwrt_package\n",
        f"{indent}  vars:\n",
        f"{indent}    openwrt_package_names: {normalize_package_expression(package_expression)}\n",
        f"{indent}    openwrt_package_state: {state}\n",
        f"{indent}    openwrt_package_update_cache: {update_cache or default_update_cache(state)}\n",
    ]
    for chunk in preserved:
        converted.extend(chunk)
    return converted


def convert_text(
    text: str,
    task_name: str,
    package_expression: str | None,
    state: str,
    update_cache: str | None,
) -> str:
    lines = text.splitlines(keepends=True)
    block = find_task_block(lines, task_name)
    converted = convert_block(block, package_expression, state, update_cache)
    return "".join(lines[: block.start] + converted + lines[block.end :])


def run_self_test() -> None:
    with TemporaryDirectory() as tmp:
        task_file = Path(tmp) / "tasks.yml"
        source = """---
- name: Example packages を導入
  ansible.builtin.opkg:
    name: "{{ item }}"
    state: present
    update_cache: "{{ openwrt_opkg_update | default(true) }}"
  loop: "{{ openwrt_example_packages }}"
  loop_control:
    label: "{{ item }}"
  when: example_enabled | bool
  tags:
    - example
"""
        task_file.write_text(source, encoding="utf-8")
        converted = convert_text(
            task_file.read_text(encoding="utf-8"),
            "Example packages を導入",
            None,
            "present",
            None,
        )
        assert "ansible.builtin.include_role:" in converted
        assert 'openwrt_package_names: "{{ openwrt_example_packages }}"' in converted
        assert "loop:" not in converted
        assert "loop_control:" not in converted
        assert "when: example_enabled | bool" in converted
        assert "tags:" in converted

        remove_source = """---
- name: dnsmasq をアンインストール
  ansible.builtin.shell: |
    opkg remove dnsmasq >/tmp/opkg-remove-dnsmasq.log 2>&1 || true
    cat /tmp/opkg-remove-dnsmasq.log
  register: remove_result
  changed_when: true
  failed_when: false
  tags:
    - pxe_dnsmasq
"""
        converted_remove = convert_text(
            remove_source,
            "dnsmasq をアンインストール",
            None,
            "absent",
            None,
        )
        assert "ansible.builtin.shell:" not in converted_remove
        assert "register:" not in converted_remove
        assert "openwrt_package_names: ['dnsmasq']" in converted_remove
        assert "openwrt_package_state: absent" in converted_remove
        assert "openwrt_package_update_cache: false" in converted_remove
        assert "tags:" in converted_remove

        nested_source = """---
- name: Parent block
  block:
    - name: Portage 取得用に git を導入 (OpenWrt 側)
      ansible.builtin.opkg:
        name: git
        state: present
        update_cache: "{{ openwrt_opkg_update | default(true) }}"
      when:
        - openwrt_gentoo_enabled | bool
"""
        converted_nested = convert_text(
            nested_source,
            "Portage 取得用に git を導入 (OpenWrt 側)",
            None,
            "present",
            None,
        )
        assert "      ansible.builtin.include_role:" in converted_nested
        assert "        name: openwrt_package" in converted_nested
        assert "        openwrt_package_names: ['git']" in converted_nested
        assert "      when:" in converted_nested
    print("convert_openwrt_package_task self-test ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        run_self_test()
        return 0

    if args.file is None or args.task_name is None:
        raise SystemExit("--file and --task-name are required unless --self-test is used")

    original = args.file.read_text(encoding="utf-8")
    converted = convert_text(
        original,
        args.task_name,
        args.package_expression,
        args.state,
        args.update_cache,
    )
    if converted == original:
        print("no changes", file=sys.stderr)
        return 0

    if args.write:
        args.file.write_text(converted, encoding="utf-8")
    else:
        sys.stdout.writelines(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                converted.splitlines(keepends=True),
                fromfile=str(args.file),
                tofile=str(args.file),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
