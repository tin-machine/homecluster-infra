#!/usr/bin/env python3
"""Check OpenCode session export for repeated tool failures."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def load_json_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"no JSON object found in {path}")


def load_jsonl_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def export_tool_events(export: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for message in export.get("messages", []):
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []):
            if isinstance(part, dict) and part.get("type") == "tool":
                tools.append(part)
    return tools


def jsonl_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for event in events:
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "tool":
            tools.append(part)
    return tools


def normalized_tool(part: dict[str, Any]) -> dict[str, Any]:
    state = part.get("state")
    if not isinstance(state, dict):
        state = {}
    input_value = state.get("input")
    if not isinstance(input_value, dict):
        input_value = {}
    return {
        "tool": part.get("tool", ""),
        "status": state.get("status", ""),
        "error": state.get("error", ""),
        "input": input_value,
    }


def stable_input(input_value: dict[str, Any]) -> str:
    return json.dumps(input_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_permission_denial(tool: dict[str, Any]) -> bool:
    if tool["status"] != "error":
        return False
    error = str(tool["error"]).lower()
    return "permission" in error or "not allowed" in error or "denied" in error or "forbidden" in error


def is_noop_edit(tool: dict[str, Any]) -> bool:
    if tool["tool"] != "edit" or tool["status"] != "error":
        return False
    input_value = tool["input"]
    old = input_value.get("oldString")
    new = input_value.get("newString")
    if old is None or new is None or old != new:
        return False
    error = str(tool["error"])
    return "No changes to apply" in error or "oldString and newString are identical" in error


def max_consecutive(values: list[str]) -> tuple[str, int]:
    best_value = ""
    best_count = 0
    current_value = ""
    current_count = 0
    for value in values:
        if value == current_value:
            current_count += 1
        else:
            current_value = value
            current_count = 1
        if current_count > best_count:
            best_value = current_value
            best_count = current_count
    return best_value, best_count


def check_tools(raw_tools: list[dict[str, Any]]) -> dict[str, Any]:
    tools = [normalized_tool(part) for part in raw_tools]
    tool_counts = Counter(str(tool["tool"]) for tool in tools)
    error_tools = [tool for tool in tools if tool["status"] == "error"]
    permission_denials = [tool for tool in tools if is_permission_denial(tool)]
    noop_edits = [tool for tool in tools if is_noop_edit(tool)]

    error_signatures = [
        "\t".join([str(tool["tool"]), str(tool["status"]), str(tool["error"]), stable_input(tool["input"])])
        for tool in error_tools
    ]
    noop_signatures = [
        "\t".join([str(tool["tool"]), str(tool["error"]), stable_input(tool["input"])])
        for tool in noop_edits
    ]
    repeated_error_signature, repeated_error_count = max_consecutive(error_signatures)
    repeated_noop_signature, repeated_noop_count = max_consecutive(noop_signatures)

    result: dict[str, Any] = {
        "ok": True,
        "failed_step": None,
        "summary": "session tool trace passed wrapper gates",
        "tool_counts": dict(sorted(tool_counts.items())),
        "error_tool_count": len(error_tools),
        "permission_denial_count": len(permission_denials),
        "noop_edit_error_count": len(noop_edits),
        "max_repeated_error_count": repeated_error_count,
        "max_repeated_noop_edit_count": repeated_noop_count,
    }

    if permission_denials:
        result.update(
            ok=False,
            failed_step="permission-denied-tool",
            summary=f"OpenCode attempted a denied tool: {permission_denials[0]['tool']}",
        )
    elif repeated_noop_count >= 2:
        result.update(
            ok=False,
            failed_step="tool-loop",
            summary="OpenCode repeated no-op edit tool errors",
            repeated_signature=repeated_noop_signature,
        )
    elif repeated_error_count >= 3:
        result.update(
            ok=False,
            failed_step="tool-loop",
            summary="OpenCode repeated the same tool error",
            repeated_signature=repeated_error_signature,
        )

    return result


def run_check(session_export: Path, events_jsonl: Path | None) -> dict[str, Any]:
    raw_tools: list[dict[str, Any]] = []
    try:
        export = load_json_object(session_export)
    except ValueError:
        export = {}
    if export:
        raw_tools = export_tool_events(export)
    if not raw_tools and events_jsonl is not None and events_jsonl.exists():
        raw_tools = jsonl_tool_events(load_jsonl_events(events_jsonl))
    return check_tools(raw_tools)


def self_test() -> None:
    noop_tool = {
        "type": "tool",
        "tool": "edit",
        "state": {
            "status": "error",
            "input": {"filePath": "opencode.json", "oldString": "", "newString": ""},
            "error": "No changes to apply: oldString and newString are identical.",
        },
    }
    ok_tool = {
        "type": "tool",
        "tool": "edit",
        "state": {
            "status": "completed",
            "input": {"filePath": "README.md", "oldString": "old", "newString": "new"},
        },
    }
    denied_tool = {
        "type": "tool",
        "tool": "bash",
        "state": {
            "status": "error",
            "input": {"command": "ansible --version"},
            "error": "Permission denied by project policy.",
        },
    }
    assert check_tools([ok_tool])["ok"] is True
    noop_result = check_tools([noop_tool, noop_tool])
    assert noop_result["ok"] is False
    assert noop_result["failed_step"] == "tool-loop"
    denied_result = check_tools([denied_tool])
    assert denied_result["ok"] is False
    assert denied_result["failed_step"] == "permission-denied-tool"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_export", nargs="?")
    parser.add_argument("--events-jsonl")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        print("check_opencode_session_export self-test ok")
        return 0
    if not args.session_export:
        parser.error("session_export is required unless --self-test is used")

    result = run_check(
        Path(args.session_export),
        Path(args.events_jsonl) if args.events_jsonl else None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
