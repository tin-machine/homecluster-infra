#!/usr/bin/env python3
"""Read the fixed staging Terraform completion receipt from the current boot.

This is partial evidence of successful Terraform invocations. It does not
attest current Helm values, chart health, or the current bundle selector.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable, Sequence

SCHEMA = "homecluster-terraform-apply-observation-v1"
RECEIPT_SCHEMA = "homecluster-terraform-apply-receipt-v1"
SCOPE = "terraform_apply_success_not_helm_state_attested"
RECEIPT = Path("/run/k3s-observability-apply/staging.done")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UPTIME = Path("/proc/uptime")
FIELDS = ("SCHEMA", "SCOPE", "BOOT_ID", "BUNDLE_ID", "INFRA_REVISION",
          "INVENTORY_REVISION", "APPLIED_AT_EPOCH")
BOOT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
BUNDLE = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{12}$")
MAX_BYTES = 1024


def unknown(reason: str) -> dict:
    return {"schema": SCHEMA, "status": "unknown", "reason": reason,
            "scope": SCOPE, "boot_id": None, "bundle_id": None,
            "infra_revision": None, "inventory_revision": None,
            "applied_at": None, "observed_at": None, "receipt_sha256": None}


def _read(path: Path, *, expected_uid: int) -> tuple[dict[str, str], bytes, float]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= MAX_BYTES):
            raise ValueError("receipt metadata invalid")
        raw = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != info.st_size or after.st_ino != info.st_ino
                or after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns):
            raise ValueError("receipt size changed")
    finally:
        os.close(fd)
    if not raw.endswith(b"\n") or b"\r" in raw or b"\0" in raw:
        raise ValueError("receipt newline invalid")
    lines = raw[:-1].decode("ascii").split("\n")
    if len(lines) != len(FIELDS):
        raise ValueError("receipt lines invalid")
    parsed: dict[str, str] = {}
    for key, line in zip(FIELDS, lines):
        prefix = key + "="
        if not line.startswith(prefix) or not line[len(prefix):] or len(line) > 160:
            raise ValueError("receipt field invalid")
        parsed[key] = line[len(prefix):]
    return parsed, raw, info.st_mtime


def snapshot(*, receipt_path: Path = RECEIPT, boot_path: Path = BOOT_ID,
             uptime_path: Path = UPTIME, now_fn: Callable[[], float] = time.time,
             expected_uid: int = 0) -> dict:
    """Fixture paths/clock/owner are internal; CLI accepts no path or host."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        uptime = float(uptime_path.read_text(encoding="ascii").split()[0])
        now = now_fn()
        if (BOOT.fullmatch(first) is None or not math.isfinite(uptime)
                or not math.isfinite(now) or uptime < 0 or uptime > 5 * 365 * 86400
                or now <= uptime):
            return unknown("boot_identity_invalid")
        values, raw, mtime = _read(receipt_path, expected_uid=expected_uid)
        if (values["SCHEMA"] != RECEIPT_SCHEMA or values["SCOPE"] != SCOPE
                or values["BOOT_ID"] != first
                or BUNDLE.fullmatch(values["BUNDLE_ID"]) is None
                or SHA.fullmatch(values["INFRA_REVISION"]) is None
                or SHA.fullmatch(values["INVENTORY_REVISION"]) is None
                or values["BUNDLE_ID"] != (values["INFRA_REVISION"][:12] + "-"
                                              + values["INVENTORY_REVISION"][:12])):
            return unknown("receipt_identity_invalid")
        epoch = values["APPLIED_AT_EPOCH"]
        if not epoch.isascii() or not epoch.isdigit() or len(epoch) > 16:
            return unknown("receipt_time_invalid")
        applied = int(epoch)
        if not (now - uptime - 2 <= applied <= mtime + 2 <= now + 4
                and abs(mtime - applied) <= 30):
            return unknown("receipt_time_invalid")
        second = boot_path.read_text(encoding="ascii").strip()
        if second != first:
            return unknown("boot_identity_changed")
        applied_at = datetime.fromtimestamp(applied, timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        observed_at = datetime.fromtimestamp(now, timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    except FileNotFoundError as error:
        return unknown("receipt_missing" if error.filename == str(receipt_path)
                       else "boot_identity_unavailable")
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, IndexError):
        return unknown("receipt_unavailable_or_invalid")
    return {"schema": SCHEMA, "status": "partial",
            "reason": "terraform_apply_success_receipt_observed", "scope": SCOPE,
            "boot_id": first, "bundle_id": values["BUNDLE_ID"],
            "infra_revision": values["INFRA_REVISION"],
            "inventory_revision": values["INVENTORY_REVISION"],
            "applied_at": applied_at, "observed_at": observed_at,
            "receipt_sha256": hashlib.sha256(raw).hexdigest()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed staging Terraform receipt reader")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
