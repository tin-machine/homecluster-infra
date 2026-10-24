"""Bounded subprocess and result helpers for fixed source validators."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Sequence


Runner = Callable[..., subprocess.CompletedProcess[str]]
MAX_OUTPUT_BYTES = 64 * 1024


def result(
    *,
    validator: str,
    status: str,
    reason: str,
    source_revision: str = "",
    checks: int = 0,
) -> dict[str, object]:
    return {
        "schema": 1,
        "validator": validator,
        "status": status,
        "reason": reason,
        "source_revision": source_revision,
        "checks": checks,
    }


def run(
    runner: Runner,
    command: Sequence[str],
    *,
    root: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str] | None:
    try:
        completed = runner(
            list(command),
            cwd=root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    if (
        len(stdout.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES
        or len(stderr.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES
    ):
        return None
    return completed
