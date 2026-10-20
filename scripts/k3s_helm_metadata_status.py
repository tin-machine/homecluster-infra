#!/usr/bin/env python3
"""Read only Helm Secret labels on the fixed staging server.

No Secret data, release payload, values, or kubeconfig bytes are requested.
The latest revision label is a partial observation, not chart health or
Terraform/source equivalence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Sequence

SCHEMA = "homecluster-helm-metadata-observation-v1"
SCOPE = "helm_revision_labels_not_values_or_health_attested"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
BOOT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
STATUSES = {"deployed", "superseded", "failed", "pending-install",
            "pending-upgrade", "pending-rollback", "uninstalled", "uninstalling"}
MAX_RAW_BYTES = 16 * 1024
MAX_RECORDS = 128
MAX_RELEASES = 32
COMMAND = (
    "k3s", "kubectl", "get", "secrets", "-A", "-l", "owner=helm",
    "-o", "custom-columns=NS:.metadata.namespace,NAME:.metadata.labels.name,"
          "STATUS:.metadata.labels.status,VERSION:.metadata.labels.version",
    "--no-headers",
)


def unknown(reason: str) -> dict:
    return {"schema": SCHEMA, "status": "unknown", "reason": reason,
            "scope": SCOPE, "boot_id": None, "observed_at": None,
            "revision_count": None, "failed_revision_count": None,
            "pending_count": None, "releases": []}


def _parse(raw: str) -> tuple[list[dict], int, int]:
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValueError("metadata output unavailable")
    lines = raw.splitlines()
    if not 1 <= len(lines) <= MAX_RECORDS:
        raise ValueError("metadata cardinality invalid")
    seen: set[tuple[str, str, int]] = set()
    latest: dict[tuple[str, str], dict] = {}
    failed = 0
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("metadata record invalid")
        namespace, name, status, revision_text = fields
        if (NAME.fullmatch(namespace) is None or NAME.fullmatch(name) is None
                or status not in STATUSES or not revision_text.isascii()
                or not revision_text.isdigit() or not 1 <= len(revision_text) <= 6):
            raise ValueError("metadata identity invalid")
        revision = int(revision_text)
        if (revision < 1 or str(revision) != revision_text
                or (namespace, name, revision) in seen):
            raise ValueError("metadata revision invalid")
        seen.add((namespace, name, revision))
        if status == "failed":
            failed += 1
        key = (namespace, name)
        if key not in latest or latest[key]["revision"] < revision:
            latest[key] = {"namespace": namespace, "name": name,
                           "revision": revision, "status": status}
    if len(latest) > MAX_RELEASES:
        raise ValueError("release cardinality invalid")
    releases = sorted(latest.values(), key=lambda item: (item["namespace"], item["name"]))
    pending = sum(item["status"].startswith("pending-") for item in releases)
    return releases, failed, pending


def snapshot(*, boot_path: Path = BOOT_ID,
             runner: Callable[..., Any] = subprocess.run,
             now: datetime | None = None) -> dict:
    """Fixture boot/runner may be injected; CLI exposes no host or command."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        if BOOT.fullmatch(first) is None:
            return unknown("boot_identity_invalid")
        result = runner(COMMAND, stdout=subprocess.PIPE, text=True, check=False,
                        stderr=subprocess.DEVNULL, timeout=12)
        if result.returncode != 0:
            return unknown("helm_metadata_read_unavailable")
        releases, failed, pending = _parse(result.stdout)
        second = boot_path.read_text(encoding="ascii").strip()
        if second != first:
            return unknown("boot_identity_changed")
        observed = datetime.now(timezone.utc) if now is None else now
        if observed.utcoffset() is None:
            return unknown("observation_time_invalid")
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError,
            subprocess.TimeoutExpired):
        return unknown("helm_metadata_unavailable_or_invalid")
    return {"schema": SCHEMA, "status": "partial",
            "reason": "helm_metadata_observed_no_values_attestation", "scope": SCOPE,
            "boot_id": first,
            "observed_at": observed.astimezone(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "revision_count": len(result.stdout.splitlines()),
            "failed_revision_count": failed, "pending_count": pending,
            "releases": releases}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed staging Helm metadata reader")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
