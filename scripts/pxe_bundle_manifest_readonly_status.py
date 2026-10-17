#!/usr/bin/env python3
"""Fixed-path read-only observation of an existing paired PXE bundle manifest.

This does NOT attest that a manifest was used by a particular generation run,
nor that any booted node used its artifacts. No inventory, SSH or write access.
Only a YYYYMMDD release date is accepted, never an arbitrary path or host.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Sequence

SCHEMA = "pxe-bundle-manifest-readonly-observation-v1"
SCOPE = "paired_manifest_bytes_unbound_to_generation_run"
ROOT = Path("/srv/gentoo/releases")
_DATE = re.compile(r"^[0-9]{8}$")
_RELEASE = re.compile(r"^[0-9]{8}-(rpi4|rpi5)$")
_ALLOWED_KEYS = frozenset((
    "schema", "policy", "bundle_date", "releases", "target_hosts",
    "target_stages", "required_tftp_paths", "markers", "metadata",
))
MAX_BYTES = 512 * 1024


def _date(value: Any) -> bool:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _result(date: str, reason: str, *, digest: str | None = None,
            releases: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "partial" if digest is not None else "unknown",
        "reason": reason,
        "scope": SCOPE,
        "bundle_date": date,
        "manifest_sha256": digest,
        "releases": releases,
        "generation_run_id": None,
        "source_revision": None,
    }


def _manifest(value: Any, date: str) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != _ALLOWED_KEYS:
        return None
    if type(value["schema"]) is not int or value["schema"] != 1:
        return None
    if value["policy"] != "paired_clean" or value["bundle_date"] != date:
        return None
    releases = value["releases"]
    if not isinstance(releases, dict) or set(releases) != {"rpi4", "rpi5"}:
        return None
    expected = {name: f"{date}-{name}" for name in ("rpi4", "rpi5")}
    if releases != expected:
        return None
    # These may contain private addresses/paths. Validate their container
    # shape, but never serialize values or their digest separately.
    if (
        not isinstance(value["target_hosts"], list)
        or not isinstance(value["target_stages"], list)
        or not isinstance(value["required_tftp_paths"], list)
        or not isinstance(value["markers"], list)
        or not isinstance(value["metadata"], dict)
        or any(not isinstance(x, str) for x in value["target_hosts"])
        or any(not isinstance(x, str) for x in value["target_stages"])
    ):
        return None
    return expected


def inspect(*, bundle_date: str, root: Path = ROOT) -> dict[str, Any]:
    """For production the CLI fixes root; test fixtures may replace it."""
    if not _date(bundle_date):
        return _result("", "bundle_date_invalid")
    path = root / (bundle_date + ".json")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return _result(bundle_date, "manifest_missing")
    except OSError:
        return _result(bundle_date, "manifest_unavailable")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            return _result(bundle_date, "manifest_invalid")
        payload = os.read(fd, MAX_BYTES + 1)
        if len(payload) != info.st_size:
            return _result(bundle_date, "manifest_changed_during_read")
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ):
            return _result(bundle_date, "manifest_changed_during_read")
    except OSError:
        return _result(bundle_date, "manifest_unavailable")
    finally:
        os.close(fd)
    try:
        data = json.loads(payload)
    except (UnicodeError, ValueError):
        return _result(bundle_date, "manifest_invalid")
    releases = _manifest(data, bundle_date)
    if releases is None:
        return _result(bundle_date, "manifest_contract_invalid")
    return _result(
        bundle_date, "manifest_bytes_observed_run_binding_unknown",
        digest=hashlib.sha256(payload).hexdigest(), releases=releases,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed PXE bundle manifest read-only digest")
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--bundle-date", required=True)
    args = parser.parse_args(argv)
    result = inspect(bundle_date=args.bundle_date)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
