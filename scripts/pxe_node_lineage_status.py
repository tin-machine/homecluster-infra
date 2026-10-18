#!/usr/bin/env python3
"""Read-only, fixed-path PXE Gentoo node lineage snapshot (L3-B1).

Run ON a booted node, never on the OpenWrt builder.  This probe observes the
current checkout HEAD but does not claim it was the commit executed by the
previous ansible-pull.  Output is bounded and contains no host/IP/path/log.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UPTIME = Path("/proc/uptime")
LAST_ENV = Path("/run/ansible-pull/last.env")
CHECKOUT = Path("/var/lib/ansible-pull")
SCHEMA = "pxe-node-lineage-local-observation-v1"
SHA = re.compile(r"^[0-9a-f]{40}$")
BOOT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STATE_KEYS = frozenset({"ROLE", "STAGE", "BRANCH", "RC", "ELAPSED_SECS", "STEP"})
MAX_STATE_BYTES = 2048
GIT_TIMEOUT = 5


def _terminal(reason: str, *, boot_id: str = "", checkout_head: str = "",
              pull_state: str = "unknown", observed_at: str = "") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "partial" if reason == "checkout_head_observed_not_execution_attested" else "unknown",
        "reason": reason,
        "source_kind": "current_checkout_head_not_executed_revision",
        "boot_id": boot_id,
        "checkout_head": checkout_head,
        "ansible_pull_state": pull_state,
        "observed_at": observed_at,
        "boot_selector": None,
        "executed_ansible_pull_revision": None,
    }


def _state(path: Path, *, boot_epoch: float, now: float) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
            return "unknown"
        if not boot_epoch <= info.st_mtime <= now + 2:
            return "unknown"
        raw = path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return "unknown"
    lines = raw.splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            return "unknown"
        key, value = line.split("=", 1)
        if key not in STATE_KEYS or key in parsed or "\x00" in value or len(value) > 128:
            return "unknown"
        parsed[key] = value
    if set(parsed) != STATE_KEYS or parsed["STAGE"] not in {"stg", "staging"} or parsed["BRANCH"] != "stg":
        return "unknown"
    if not parsed["RC"].isdigit() or not parsed["ELAPSED_SECS"].isdigit():
        return "unknown"
    if parsed["RC"] == "0" and parsed["STEP"] == "complete":
        return "completed"
    return "not_completed"


def _git_checkout_head(path: Path, runner: Callable[[Sequence[str]], Any]) -> str:
    # A git worktree and arbitrary checkout path are not accepted here.
    try:
        repository = path.lstat()
        dotgit = (path / ".git").lstat()
    except OSError:
        return ""
    if not stat.S_ISDIR(repository.st_mode) or not stat.S_ISDIR(dotgit.st_mode):
        return ""
    try:
        result = runner(("git", "-C", str(path), "rev-parse", "--verify", "HEAD^{commit}"))
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    sha = str(result.stdout).strip()
    return sha if SHA.fullmatch(sha) else ""


def _runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), check=False, timeout=GIT_TIMEOUT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def snapshot(*, boot_path: Path = BOOT_ID, uptime_path: Path = UPTIME,
             state_path: Path = LAST_ENV, checkout_path: Path = CHECKOUT,
             now_fn: Callable[[], float] = time.time,
             runner: Callable[[Sequence[str]], Any] = _runner) -> dict[str, Any]:
    """Path injection is for source-only tests, never exposed by the CLI."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        uptime_text = uptime_path.read_text(encoding="ascii").split()[0]
        uptime = float(uptime_text)
        now = now_fn()
    except (OSError, UnicodeError, IndexError, ValueError, OverflowError):
        return _terminal("boot_identity_unavailable")
    if BOOT.fullmatch(first) is None or not 0 <= uptime <= 60 * 60 * 24 * 365 * 5:
        return _terminal("boot_identity_invalid")
    if not now > uptime:
        return _terminal("boot_time_invalid")
    boot_epoch = now - uptime
    state = _state(state_path, boot_epoch=boot_epoch - 2, now=now)
    checkout = _git_checkout_head(checkout_path, runner)
    try:
        second = boot_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return _terminal("boot_identity_changed")
    if second != first:
        return _terminal("boot_identity_changed")
    observed = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if state == "unknown":
        return _terminal("ansible_pull_state_unavailable", boot_id=first, checkout_head=checkout,
                         observed_at=observed)
    if not checkout:
        return _terminal("checkout_head_unavailable", boot_id=first, pull_state=state, observed_at=observed)
    return _terminal("checkout_head_observed_not_execution_attested", boot_id=first,
                     checkout_head=checkout, pull_state=state, observed_at=observed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed read-only local PXE node source snapshot")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
