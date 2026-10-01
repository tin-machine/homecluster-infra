#!/usr/bin/env python3
"""Fixed source-only validator for the standard PXE generation mechanism."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = "infra-standard-pxe-source-v1"
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_MAX_OUTPUT_BYTES = 64 * 1024

SOURCE_PATHS = (
    Path("ansible/openwrt/playbooks/pxe-release-bundle-staging.yml"),
    Path("ansible/openwrt/roles/openwrt_gentoo_rootfs/defaults/main.yml"),
    Path("scripts/pxe_release_identity_status.py"),
    Path("scripts/pi-pxe-release-identity-status"),
    Path("scripts/ci/check-pxe-initramfs-contract.py"),
    Path("scripts/ci/check-pxe-shared-lower-hostname.py"),
    Path("scripts/ci/check-openwrt-gentoo-binary-preseed.py"),
    Path("scripts/ci/check-openwrt-pxe-ansible-pull-chain.py"),
    Path("scripts/ci/check-openwrt-pxe-client-catalog.py"),
    Path("scripts/ci/check-pxe-release-identity-status.py"),
)

CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "pxe-initramfs-contract",
        (sys.executable, "scripts/ci/check-pxe-initramfs-contract.py"),
    ),
    (
        "pxe-shared-lower-hostname",
        (
            sys.executable,
            "scripts/ci/check-pxe-shared-lower-hostname.py",
            "--self-test",
        ),
    ),
    (
        "openwrt-gentoo-binary-preseed",
        (
            sys.executable,
            "scripts/ci/check-openwrt-gentoo-binary-preseed.py",
            "--self-test",
        ),
    ),
    (
        "openwrt-pxe-ansible-pull-chain",
        (
            sys.executable,
            "scripts/ci/check-openwrt-pxe-ansible-pull-chain.py",
        ),
    ),
    (
        "openwrt-pxe-client-catalog",
        (
            sys.executable,
            "scripts/ci/check-openwrt-pxe-client-catalog.py",
        ),
    ),
    (
        "pxe-release-identity-status",
        (
            sys.executable,
            "scripts/ci/check-pxe-release-identity-status.py",
        ),
    ),
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _result(
    *,
    status: str,
    reason: str,
    source_revision: str = "",
    checks: int = 0,
) -> dict[str, object]:
    return {
        "schema": 1,
        "validator": VALIDATOR,
        "status": status,
        "reason": reason,
        "source_revision": source_revision,
        "checks": checks,
    }


def _run(
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
        len(stdout.encode("utf-8", errors="replace")) > _MAX_OUTPUT_BYTES
        or len(stderr.encode("utf-8", errors="replace")) > _MAX_OUTPUT_BYTES
    ):
        return None
    return completed


def validate_source(
    *,
    repo_root: Path = ROOT,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    root = repo_root.resolve()

    for relative in SOURCE_PATHS:
        path = root / relative
        try:
            metadata = path.lstat()
        except OSError:
            return _result(
                status="blocked",
                reason="infra_standard_pxe_source_path_missing",
            )
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return _result(
                status="blocked",
                reason="infra_standard_pxe_source_path_invalid",
            )

    revision_result = _run(
        runner,
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        root=root,
        timeout=30,
    )
    if revision_result is None or revision_result.returncode != 0:
        return _result(
            status="unknown",
            reason="infra_standard_pxe_source_revision_unavailable",
        )
    revision = revision_result.stdout.strip().lower()
    if _GIT_REVISION.fullmatch(revision) is None:
        return _result(
            status="unknown",
            reason="infra_standard_pxe_source_revision_invalid",
        )

    status_result = _run(
        runner,
        (
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ),
        root=root,
        timeout=30,
    )
    if status_result is None or status_result.returncode != 0:
        return _result(
            status="unknown",
            reason="infra_standard_pxe_source_status_unavailable",
            source_revision=revision,
        )
    if status_result.stdout.strip():
        return _result(
            status="blocked",
            reason="infra_standard_pxe_repository_dirty",
            source_revision=revision,
        )

    completed_checks = 0
    for _name, command in CHECKS:
        completed = _run(
            runner,
            command,
            root=root,
            timeout=300,
        )
        if completed is None:
            return _result(
                status="unknown",
                reason="infra_standard_pxe_source_check_unavailable",
                source_revision=revision,
                checks=completed_checks,
            )
        if completed.returncode != 0:
            return _result(
                status="blocked",
                reason="infra_standard_pxe_source_check_failed",
                source_revision=revision,
                checks=completed_checks,
            )
        completed_checks += 1

    return _result(
        status="pass",
        reason="infra_standard_pxe_source_validated",
        source_revision=revision,
        checks=completed_checks,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true", required=True)
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)

    result = validate_source()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"pass": 0, "blocked": 2, "unknown": 3}.get(str(result["status"]), 3)


if __name__ == "__main__":
    raise SystemExit(main())
