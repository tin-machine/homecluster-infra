"""Process lifecycle helpers shared by staging upgrade CI fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


def run_captured(
    command: Sequence[str], *, data: str | None = None,
    timeout: int, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, input=data, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, cwd=cwd,
    )


def stop_process(process: subprocess.Popen[object], *, timeout: int = 10) -> None:
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
