#!/usr/bin/env python3
"""Check Markdown style issues in added lines only."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def default_base_ref() -> str | None:
    base_ref = "origin/main"
    if git_ref_exists(base_ref):
        merge_base = git("merge-base", base_ref, "HEAD").stdout.strip()
        return merge_base or None
    if git_ref_exists("HEAD^"):
        return "HEAD^"
    return None


def changed_files(base: str | None) -> list[str]:
    names: set[str] = set()
    if base:
        names.update(git("diff", "--name-only", "--diff-filter=ACMRT", f"{base}..HEAD", "--").stdout.splitlines())
    names.update(git("diff", "--name-only", "--diff-filter=ACMRT", "HEAD", "--").stdout.splitlines())
    names.update(git("ls-files", "--others", "--exclude-standard").stdout.splitlines())
    return sorted(path for path in names if is_markdown(path) and Path(path).is_file())


def is_markdown(path: str) -> bool:
    name = Path(path).name
    return path.endswith(".md") or name == "README" or name.startswith("README.")


def added_lines_from_diff(diff_text: str) -> set[int]:
    added: set[int] = set()
    new_line = 0
    for line in diff_text.splitlines():
        match = HUNK_RE.match(line)
        if match:
            new_line = int(match.group(1))
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue
        else:
            new_line += 1
    return added


def added_line_numbers(path: str, base: str | None, untracked: set[str]) -> set[int]:
    if path in untracked:
        return set(range(1, len(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()) + 1))

    added: set[int] = set()
    if base:
        diff = git("diff", "--unified=0", "--diff-filter=ACMRT", f"{base}..HEAD", "--", path).stdout
        added.update(added_lines_from_diff(diff))
    diff = git("diff", "--unified=0", "--diff-filter=ACMRT", "HEAD", "--", path).stdout
    added.update(added_lines_from_diff(diff))
    return added


def fenced_lines(lines: list[str]) -> set[int]:
    fenced: set[int] = set()
    in_fence = False
    fence_marker = ""
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        is_fence = indent <= 3 and (stripped.startswith("```") or stripped.startswith("~~~"))
        if is_fence:
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            fenced.add(number)
            continue
        if in_fence:
            fenced.add(number)
    return fenced


def check_file(path: str, added: set[int]) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    fenced = fenced_lines(lines)
    failures: list[str] = []
    for number in sorted(added):
        if number < 1 or number > len(lines) or number in fenced:
            continue
        line = lines[number - 1]
        if re.match(r"^ {3}\S", line):
            failures.append(
                f"{path}:{number}: avoid exactly 3 leading spaces in Markdown; "
                "use 2 spaces for list continuation or 4 spaces/fenced code for code blocks"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="Markdown files to check. Defaults to changed markdown files.")
    parser.add_argument("--base-ref", default=None, help="Base ref or commit for committed changes.")
    args = parser.parse_args()

    base = args.base_ref or default_base_ref()
    untracked = set(git("ls-files", "--others", "--exclude-standard").stdout.splitlines())
    files = [path for path in args.files if is_markdown(path) and Path(path).is_file()] or changed_files(base)

    failures: list[str] = []
    for path in files:
        failures.extend(check_file(path, added_line_numbers(path, base, untracked)))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
