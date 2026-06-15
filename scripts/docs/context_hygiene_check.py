#!/usr/bin/env python3
"""Read-only hygiene report for always-loaded AI context docs.

This script intentionally does not edit files. It reports threshold overages and
move candidates so Codex can raise them as consultation items in the final reply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PATHS = ("docs/ai-context.md", "docs/memory.md")

DEFAULT_HYGIENE: dict[str, dict[str, int]] = {
    "current-context": {
        "max_lines_warn": 300,
        "max_lines_error": 450,
        "long_item_warn_chars": 1200,
        "dated_detail_days": 30,
        "review_after_days": 60,
        "max_candidates": 12,
    },
    "memory-index": {
        "max_lines_warn": 800,
        "max_lines_error": 1200,
        "long_item_warn_chars": 1600,
        "dated_detail_days": 180,
        "review_after_days": 120,
        "max_candidates": 12,
    },
    "default": {
        "max_lines_warn": 1000,
        "max_lines_error": 1500,
        "long_item_warn_chars": 1800,
        "dated_detail_days": 180,
        "review_after_days": 180,
        "max_candidates": 12,
    },
}

DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|20\d{6})\b")
TEMP_DETAIL_RE = re.compile(
    r"\b(Job|Pod|Deployment|Service|PVC|Kaniko|prefix|digest|sha256|image|replicas)\b",
    re.IGNORECASE,
)
FAILURE_RE = re.compile(
    r"(failed|failure|error|panic|OOM|CrashLoop|ImagePullBackOff|失敗|エラー|異常|枯渇)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Item:
    line: int
    text: str
    kind: str


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Path
    line: int
    reason: str
    suggested_target: str
    snippet: str


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value.startswith(("'", '"'))
    ):
        return value[1:-1]
    return value


def parse_front_matter(text: str) -> tuple[dict[str, Any], str, int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 1

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, text, 1

    data: dict[str, Any] = {}
    parent: str | None = None
    for raw_line in lines[1:end_index]:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith("  ") and parent:
            child = raw_line.strip()
            if ":" not in child:
                continue
            key, value = child.split(":", 1)
            parent_value = data.setdefault(parent, {})
            if isinstance(parent_value, dict):
                parent_value[key.strip()] = parse_scalar(value)
            continue

        if ":" not in raw_line:
            parent = None
            continue

        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            data[key] = {}
            parent = key
        else:
            data[key] = parse_scalar(value)
            parent = None

    body = "\n".join(lines[end_index + 1 :])
    if text.endswith("\n"):
        body += "\n"
    return data, body, end_index + 2


def merge_hygiene(front_matter: dict[str, Any]) -> dict[str, int]:
    lifecycle = str(front_matter.get("lifecycle") or "default")
    merged = dict(DEFAULT_HYGIENE.get(lifecycle, DEFAULT_HYGIENE["default"]))
    hygiene = front_matter.get("hygiene")
    if isinstance(hygiene, dict):
        for key, value in hygiene.items():
            if isinstance(value, int):
                merged[key] = value
            elif isinstance(value, str) and re.fullmatch(r"\d+", value):
                merged[key] = int(value)
    return merged


def count_lines(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def human_size(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KiB"
    return f"{byte_count / 1024 / 1024:.2f} MiB"


def normalize_snippet(text: str, limit: int = 120) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def parse_date(value: str) -> dt.date | None:
    try:
        if "-" in value:
            return dt.date.fromisoformat(value)
        return dt.date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


def extract_items(body: str) -> list[Item]:
    items: list[Item] = []
    current_line: int | None = None
    current_parts: list[str] = []
    current_kind = "paragraph"
    in_code = False

    def flush() -> None:
        nonlocal current_line, current_parts, current_kind
        if current_line is not None and current_parts:
            text = " ".join(part.strip() for part in current_parts if part.strip())
            if text:
                items.append(Item(current_line, text, current_kind))
        current_line = None
        current_parts = []
        current_kind = "paragraph"

    for lineno, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            continue

        bullet = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if bullet:
            flush()
            current_line = lineno
            current_parts = [bullet.group(3)]
            current_kind = "list-item"
            continue

        if current_line is not None and (line.startswith((" ", "\t"))):
            current_parts.append(stripped)
            continue

        if current_line is None:
            current_line = lineno
            current_parts = [stripped]
            current_kind = "paragraph"
        else:
            current_parts.append(stripped)

    flush()
    return items


def suggested_target_for(item: Item, lifecycle: str, reason: str) -> str:
    text = item.text
    if "current-context" == lifecycle:
        if TEMP_DETAIL_RE.search(text):
            return "docs/operation/ or docs/memory.md"
        if FAILURE_RE.search(text):
            return "docs/troubleshooting/known-issues.md or docs/troubleshooting/"
        if "設計" in text or "方針" in text or "採用" in text or "不採用" in text:
            return "docs/system/ or docs/operation/"
        if "dated" in reason or "日付" in reason:
            return "docs/memory.md or a linked operation doc"
    if "memory-index" == lifecycle and len(text) > 1000:
        return "keep a short memory entry and link to operation/troubleshooting docs"
    return "review manually"


def analyze_file(path: Path, today: dt.date) -> tuple[dict[str, Any], list[Finding]]:
    text = path.read_text(encoding="utf-8")
    front_matter, body, body_start_line = parse_front_matter(text)
    lifecycle = str(front_matter.get("lifecycle") or "default")
    hygiene = merge_hygiene(front_matter)
    findings: list[Finding] = []

    line_count = count_lines(text)
    if line_count >= hygiene["max_lines_error"]:
        findings.append(
            Finding(
                "error",
                path,
                1,
                f"line count {line_count} >= error threshold {hygiene['max_lines_error']}",
                "split or compact this document",
                path.name,
            )
        )
    elif line_count >= hygiene["max_lines_warn"]:
        findings.append(
            Finding(
                "warning",
                path,
                1,
                f"line count {line_count} >= warning threshold {hygiene['max_lines_warn']}",
                "consider moving completed detail out",
                path.name,
            )
        )

    last_reviewed = front_matter.get("last_reviewed")
    if isinstance(last_reviewed, str):
        reviewed_date = parse_date(last_reviewed)
        if reviewed_date:
            age_days = (today - reviewed_date).days
            if age_days >= hygiene["review_after_days"]:
                findings.append(
                    Finding(
                        "warning",
                        path,
                        1,
                        f"last_reviewed is {age_days} days old",
                        "review and refresh front matter if still current",
                        f"last_reviewed: {last_reviewed}",
                    )
                )

    for item in extract_items(body):
        if len(item.text) >= hygiene["long_item_warn_chars"]:
            reason = (
                f"{item.kind} is {len(item.text)} chars "
                f">= warning threshold {hygiene['long_item_warn_chars']}"
            )
            findings.append(
                Finding(
                    "warning",
                    path,
                    body_start_line + item.line - 1,
                    reason,
                    suggested_target_for(item, lifecycle, reason),
                    normalize_snippet(item.text),
                )
            )

        if lifecycle == "current-context":
            text_without_code = re.sub(r"`[^`]+`", "", item.text)
            for match in DATE_RE.finditer(text_without_code):
                date_value = parse_date(match.group(1))
                if date_value is None:
                    continue
                age_days = (today - date_value).days
                if age_days >= hygiene["dated_detail_days"]:
                    reason = (
                        f"dated detail {match.group(1)} is {age_days} days old "
                        f">= candidate threshold {hygiene['dated_detail_days']}"
                    )
                    findings.append(
                        Finding(
                            "suggestion",
                            path,
                            body_start_line + item.line - 1,
                            reason,
                            suggested_target_for(item, lifecycle, reason),
                            normalize_snippet(item.text),
                        )
                    )
                    break

    summary = {
        "path": path,
        "front_matter": front_matter,
        "hygiene": hygiene,
        "line_count": line_count,
        "byte_count": len(text.encode("utf-8")),
        "lifecycle": lifecycle,
    }
    return summary, findings


def print_report(
    summaries: list[dict[str, Any]],
    findings_by_path: dict[Path, list[Finding]],
) -> None:
    total_errors = sum(
        1
        for findings in findings_by_path.values()
        for finding in findings
        if finding.severity == "error"
    )
    total_warnings = sum(
        1
        for findings in findings_by_path.values()
        for finding in findings
        if finding.severity == "warning"
    )
    total_suggestions = sum(
        1
        for findings in findings_by_path.values()
        for finding in findings
        if finding.severity == "suggestion"
    )

    print("Docs context hygiene report")
    print(f"Summary: {total_errors} errors, {total_warnings} warnings, {total_suggestions} suggestions")
    print("Mode: read-only; no files changed")
    print()

    for summary in summaries:
        path = summary["path"]
        hygiene = summary["hygiene"]
        print(f"{path}:")
        print(
            f"  lifecycle={summary['lifecycle']} "
            f"lines={summary['line_count']} "
            f"size={human_size(summary['byte_count'])}"
        )
        print(
            "  thresholds: "
            f"warn={hygiene['max_lines_warn']} "
            f"error={hygiene['max_lines_error']} "
            f"long_item={hygiene['long_item_warn_chars']}"
        )

        findings = findings_by_path.get(path, [])
        if not findings:
            print("  OK: no findings")
            print()
            continue

        max_candidates = hygiene["max_candidates"]
        for finding in findings[:max_candidates]:
            print(f"  - {finding.severity}: line {finding.line}: {finding.reason}")
            print(f"    suggested_target: {finding.suggested_target}")
            print(f"    snippet: {finding.snippet}")
        remaining = len(findings) - max_candidates
        if remaining > 0:
            print(f"  ... {remaining} more findings hidden by max_candidates={max_candidates}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only context hygiene report for AI-facing docs."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help="Markdown files to check. Defaults to docs/ai-context.md and docs/memory.md.",
    )
    parser.add_argument(
        "--today",
        metavar="YYYY-MM-DD",
        help="Override current date for reproducible reports.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit non-zero only when an error finding is present.",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    today = dt.date.today()
    if args.today:
        try:
            today = dt.date.fromisoformat(args.today)
        except ValueError:
            print(f"invalid --today value: {args.today}", file=sys.stderr)
            return 2

    summaries: list[dict[str, Any]] = []
    findings_by_path: dict[Path, list[Finding]] = {}

    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.exists():
            print(f"missing file: {path}", file=sys.stderr)
            return 2
        summary, findings = analyze_file(path, today)
        summaries.append(summary)
        findings_by_path[path] = findings

    print_report(summaries, findings_by_path)

    has_error = any(
        finding.severity == "error"
        for findings in findings_by_path.values()
        for finding in findings
    )
    if has_error and args.fail_on_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
