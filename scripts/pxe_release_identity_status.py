#!/usr/bin/env python3
"""Read-only standard PXE release identity collision probe.

The public mechanism consumes the repository-standard generated inventory
entrypoint and checks only the fixed staging release identity plus immutable
PXE release paths. It never mutates inventory or remote state and emits only
the bounded controller probe contract.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

GROUP_NAME = "openwrt"
STAGE_NAME = "stg"
MARKER = "PXE_IDENTITY_COLLISION"
_CANDIDATE_RE = re.compile(r"^[0-9]{8}$")
_MARKER_RE = re.compile(r"PXE_IDENTITY_COLLISION=([01])")
_TIMEOUT_SECONDS = 30

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]


def terminal(status: str, reason: str, candidate: str) -> dict[str, str]:
    if status not in {"pass", "blocked", "unknown"}:
        raise ValueError("status_invalid")
    if reason not in {"identity_available", "identity_in_use", "probe_blocked", "source_unavailable"}:
        raise ValueError("reason_invalid")
    return {
        "status": status,
        "reason": reason,
        "candidate": candidate,
    }


def probe_candidate(
    candidate: str,
    *,
    inventory_path: Path,
    runner: Runner | None = None,
) -> dict[str, str]:
    if not _candidate_valid(candidate):
        return terminal("blocked", "probe_blocked", candidate)

    invoke = _run if runner is None else runner
    current_identity = _current_inventory_identity(
        inventory_path=inventory_path,
        runner=invoke,
    )
    if current_identity is None:
        return terminal("unknown", "source_unavailable", candidate)
    if candidate <= current_identity:
        return terminal("blocked", "identity_in_use", candidate)

    collision = _remote_collision(
        candidate,
        inventory_path=inventory_path,
        runner=invoke,
    )
    if collision is None:
        return terminal("unknown", "source_unavailable", candidate)
    if collision:
        return terminal("blocked", "identity_in_use", candidate)
    return terminal("pass", "identity_available", candidate)


def _current_inventory_identity(
    *,
    inventory_path: Path,
    runner: Runner,
) -> str | None:
    listed = runner(
        (
            "ansible-inventory",
            "-i",
            str(inventory_path),
            "--list",
        ),
        _TIMEOUT_SECONDS,
    )
    if listed.returncode != 0:
        return None
    inventory = _json_object(listed.stdout)
    if inventory is None:
        return None

    group = inventory.get(GROUP_NAME)
    if not isinstance(group, dict):
        return None
    hosts = group.get("hosts")
    if (
        not isinstance(hosts, list)
        or len(hosts) != 1
        or not isinstance(hosts[0], str)
        or not hosts[0]
    ):
        return None

    host = hosts[0]
    resolved = runner(
        (
            "ansible-inventory",
            "-i",
            str(inventory_path),
            "--host",
            host,
        ),
        _TIMEOUT_SECONDS,
    )
    if resolved.returncode != 0:
        return None
    hostvars = _json_object(resolved.stdout)
    if hostvars is None:
        return None

    stage_dates = hostvars.get("openwrt_gentoo_release_bundle_stage_dates")
    if not isinstance(stage_dates, dict):
        return None
    current = stage_dates.get(STAGE_NAME)
    if type(current) not in {str, int}:
        return None
    normalized = str(current)
    if not _candidate_valid(normalized):
        return None
    return normalized


def _remote_collision(
    candidate: str,
    *,
    inventory_path: Path,
    runner: Runner,
) -> bool | None:
    command = _remote_probe_command(candidate)
    completed = runner(
        (
            "ansible",
            "-i",
            str(inventory_path),
            GROUP_NAME,
            "-m",
            "raw",
            "-a",
            command,
            "-o",
        ),
        _TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        return None

    matches = _MARKER_RE.findall(completed.stdout)
    if len(matches) != 1:
        return None
    return matches[0] == "1"


def _remote_probe_command(candidate: str) -> str:
    if not _candidate_valid(candidate):
        raise ValueError("candidate_invalid")
    paths = (
        f"/srv/gentoo/releases/{candidate}.json",
        f"/srv/gentoo/{candidate}-rpi4",
        f"/srv/gentoo/{candidate}-rpi5",
        f"/srv/gentoo/tftp-root/dates/{candidate}-rpi4",
        f"/srv/gentoo/tftp-root/dates/{candidate}-rpi5",
    )
    quoted_paths = " ".join(f"'{path}'" for path in paths)
    return (
        "set -eu; "
        "collision=0; "
        f"for path in {quoted_paths}; do "
        'if [ -e "$path" ] || [ -L "$path" ]; then collision=1; fi; '
        "done; "
        f'printf "{MARKER}=%s\\n" "$collision"'
    )


def _candidate_valid(value: object) -> bool:
    if not isinstance(value, str) or _CANDIDATE_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(list(command), 127, "", "")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Check whether one standard PXE release identity is unused."
    )
    value.add_argument("--candidate", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    result = probe_candidate(
        args.candidate,
        inventory_path=repo_root.parent / "inventory.yml",
    )
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return {"pass": 0, "blocked": 2, "unknown": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
