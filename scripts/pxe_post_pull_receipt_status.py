#!/usr/bin/env python3
"""Fixed, read-only per-role PXE post-pull receipt reader.

Run on a booted staging Pi with permission to read /run/ansible-pull.
This reports the SHA observed AFTER an ansible-pull returned successfully.
It does not attest to the revision that ansible-pull actually executed.
The v1 pxe_node_lineage_status.py contract is intentionally unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable, Sequence

SCHEMA = "pxe-post-pull-role-receipts-v1"
SCOPE = "historical_post_success_checkout_not_executed_revision"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UPTIME = Path("/proc/uptime")
STATE_DIR = Path("/run/ansible-pull")

# Fixed roles, not host names or user-selected paths.
ROLES = (
    "base", "workstation_cli", "k3s_stg_storage",
    "k3s_stg_server", "k3s_stg_agent", "terraform_stg",
)
BOOT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_FIELDS = frozenset((
    "SCHEMA", "ROLE", "STAGE", "BRANCH", "BOOT_ID",
    "CHECKOUT_HEAD_AFTER_SUCCESS", "RECORDED_AT_EPOCH",
))
STATE_FIELDS = frozenset(("ROLE", "STAGE", "BRANCH", "RC", "ELAPSED_SECS", "STEP"))
MAX_FILE_BYTES = 2048
MAX_SKEW_SECONDS = 30


def _unknown_role(role: str, reason: str) -> dict[str, Any]:
    return {
        "role": role, "status": "unknown", "reason": reason,
        "checkout_head_after_success": None, "recorded_at": None,
    }


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "status": "unknown", "reason": reason,
        "scope": SCOPE, "boot_id": None, "observed_at": None, "roles": [],
    }


def _read_env(path: Path, fields: frozenset[str]) -> tuple[dict[str, str] | None, float | None, str]:
    """Open a fixed file without following a final symlink, never disclose text."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None, None, "receipt_missing"
    except OSError:
        return None, None, "receipt_unavailable"
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_FILE_BYTES
            or info.st_mode & 0o022
        ):
            return None, None, "receipt_invalid"
        data = os.read(fd, MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return None, None, "receipt_invalid"
        raw = data.decode("ascii")
    except (OSError, UnicodeError):
        return None, None, "receipt_invalid"
    finally:
        os.close(fd)
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            return None, None, "receipt_invalid"
        key, value = line.split("=", 1)
        if key not in fields or key in parsed or not value or len(value) > 128:
            return None, None, "receipt_invalid"
        parsed[key] = value
    if set(parsed) != fields:
        return None, None, "receipt_invalid"
    return parsed, info.st_mtime, ""


def _read_role(role: str, *, state_dir: Path, boot_id: str,
               boot_epoch: float, now: float) -> dict[str, Any]:
    receipt, receipt_mtime, reason = _read_env(
        state_dir / (role + ".post-pull.env"), RECEIPT_FIELDS,
    )
    if reason:
        return _unknown_role(role, reason)
    status, status_mtime, why = _read_env(state_dir / (role + ".env"), STATE_FIELDS)
    if why:
        return _unknown_role(role, "role_state_" + why)
    assert receipt is not None and status is not None
    assert receipt_mtime is not None and status_mtime is not None
    if (
        receipt["SCHEMA"] != "1"
        or receipt["ROLE"] != role
        or status["ROLE"] != role
        or receipt["STAGE"] not in ("stg", "staging")
        or status["STAGE"] != receipt["STAGE"]
        or receipt["BRANCH"] != "stg"
        or status["BRANCH"] != "stg"
        or receipt["BOOT_ID"] != boot_id
        or SHA.fullmatch(receipt["CHECKOUT_HEAD_AFTER_SUCCESS"]) is None
        or status["RC"] != "0"
        or status["STEP"] != "complete"
        or not status["ELAPSED_SECS"].isdigit()
    ):
        return _unknown_role(role, "receipt_identity_or_status_mismatch")
    ts = receipt["RECORDED_AT_EPOCH"]
    if not ts.isascii() or not ts.isdigit() or len(ts) > 16:
        return _unknown_role(role, "receipt_time_invalid")
    recorded = int(ts)
    if not (
        boot_epoch - 2 <= status_mtime <= receipt_mtime <= now + 2
        and boot_epoch - 2 <= recorded <= now + 2
        and abs(recorded - receipt_mtime) <= MAX_SKEW_SECONDS
        and receipt_mtime - status_mtime <= MAX_SKEW_SECONDS
    ):
        return _unknown_role(role, "receipt_time_invalid")
    try:
        at = datetime.fromtimestamp(recorded, timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return _unknown_role(role, "receipt_time_invalid")
    return {
        "role": role,
        "status": "observed",
        "reason": "post_success_checkout_head_observed",
        "checkout_head_after_success": receipt["CHECKOUT_HEAD_AFTER_SUCCESS"],
        "recorded_at": at,
    }


def snapshot(*, boot_path: Path = BOOT_ID, uptime_path: Path = UPTIME,
             state_dir: Path = STATE_DIR,
             now_fn: Callable[[], float] = time.time) -> dict[str, Any]:
    """Only test fixtures may override paths; the CLI accepts no path/role flags."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        uptime = float(uptime_path.read_text(encoding="ascii").split()[0])
        now = now_fn()
    except (OSError, UnicodeError, ValueError, IndexError, OverflowError):
        return _unknown("boot_identity_unavailable")
    if (
        BOOT.fullmatch(first) is None or not math.isfinite(now)
        or not math.isfinite(uptime) or uptime < 0 or uptime > 5 * 365 * 86400
        or now <= uptime
    ):
        return _unknown("boot_identity_invalid")
    boot_epoch = now - uptime
    roles = [
        _read_role(role, state_dir=state_dir, boot_id=first,
                   boot_epoch=boot_epoch, now=now)
        for role in ROLES
    ]
    try:
        second = boot_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return _unknown("boot_identity_changed")
    if second != first:
        return _unknown("boot_identity_changed")
    try:
        observed = datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return _unknown("observation_time_invalid")
    present = any(item["status"] == "observed" for item in roles)
    return {
        "schema": SCHEMA, "status": "partial" if present else "unknown",
        "reason": "historical_post_success_observed" if present else "no_valid_post_pull_receipts",
        "scope": SCOPE, "boot_id": first, "observed_at": observed,
        "roles": roles,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed staging post-pull receipt reader")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
