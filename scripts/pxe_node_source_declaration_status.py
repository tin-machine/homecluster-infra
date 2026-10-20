#!/usr/bin/env python3
"""Read the fixed staging boot declaration without claiming role execution.

The controller runs this public source over SSH stdin. No caller-selected path,
host, role, or expected revision is accepted by the production CLI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Sequence

SCHEMA = "pxe-node-source-declaration-observation-v1"
SCOPE = "current_boot_declaration_not_role_execution"
DECLARATION = Path("/etc/ansible-pull/source-declaration.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
MAX_BYTES = 4096
BOOT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
BUILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FIELDS = {"schema", "environment", "build_id", "infra_revision"}


def unknown(reason: str) -> dict:
    return {"schema": SCHEMA, "status": "unknown", "reason": reason,
            "scope": SCOPE, "boot_id": None, "build_id": None,
            "infra_revision": None, "declaration_sha256": None,
            "observed_at": None}


def _declaration(path: Path) -> tuple[dict, str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES
                or info.st_mode & 0o022):
            raise ValueError("declaration metadata invalid")
        raw = os.read(fd, MAX_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("declaration size changed")
    finally:
        os.close(fd)

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate declaration key")
            value[key] = item
        return value

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != FIELDS
            or value["schema"] != "homecluster-pxe-source-declaration-v1"
            or value["environment"] != "staging"
            or not isinstance(value["build_id"], str)
            or BUILD.fullmatch(value["build_id"]) is None
            or not isinstance(value["infra_revision"], str)
            or SHA.fullmatch(value["infra_revision"]) is None):
        raise ValueError("declaration identity invalid")
    return value, hashlib.sha256(raw).hexdigest()


def snapshot(*, boot_path: Path = BOOT_ID,
             declaration_path: Path = DECLARATION,
             now: datetime | None = None) -> dict:
    """Fixture paths are internal only; the CLI always uses fixed paths."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        if BOOT.fullmatch(first) is None:
            return unknown("boot_identity_invalid")
        value, digest = _declaration(declaration_path)
        second = boot_path.read_text(encoding="ascii").strip()
        if second != first:
            return unknown("boot_identity_changed")
        observed = datetime.now(timezone.utc) if now is None else now
        if observed.utcoffset() is None:
            return unknown("observation_time_invalid")
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return unknown("declaration_unavailable_or_invalid")
    return {
        "schema": SCHEMA, "status": "partial",
        "reason": "pinned_declaration_observed_not_execution_attested",
        "scope": SCOPE, "boot_id": first,
        "build_id": value["build_id"],
        "infra_revision": value["infra_revision"],
        "declaration_sha256": digest,
        "observed_at": observed.astimezone(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed staging boot declaration reader")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
