#!/usr/bin/env python3
"""Fixed source-only validator for the Raspberry Pi 5 common-kernel mechanism."""

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
VALIDATOR = "infra-common-kernel-source-v1"
_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_MAX_OUTPUT_BYTES = 64 * 1024

SOURCE_PATHS = (
    Path("ansible/arm64/playbooks/rpi5-egpu-nvidia-artifact-bundle.yml"),
    Path("ansible/arm64/roles/rpi5_common_kernel_build/defaults/main.yml"),
    Path("ansible/arm64/roles/rpi5_common_kernel_build/tasks/main.yml"),
    Path("ansible/arm64/roles/rpi5_egpu_nvidia_artifact_bundle/defaults/main.yml"),
    Path("ansible/arm64/roles/rpi5_egpu_nvidia_artifact_bundle/tasks/main.yml"),
    Path("ansible/openwrt/playbooks/pxe-release-bundle-staging-with-common-kernel.yml"),
    Path("ansible/openwrt/playbooks/pxe-release-bundle-build.yml"),
    Path("ansible/openwrt/playbooks/tasks/pxe_release_bundle_immutable_preflight.yml"),
    Path("ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml"),
    Path("ansible/openwrt/playbooks/rpi5-common-kernel-precheck.yml"),
    Path("ansible/openwrt/playbooks/rpi5-common-kernel-gate.yml"),
    Path("ansible/openwrt/playbooks/rpi5-common-kernel-phase-acceptance.yml"),
    Path("ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml"),
    Path("ansible/openwrt/playbooks/rpi5-common-kernel-selector-inspect.yml"),
    Path("ansible/openwrt/playbooks/tasks/rpi5_common_kernel_live_selectors.yml"),
    Path("scripts/pi-rpi5-common-kernel-precheck"),
    Path("scripts/pi-rpi5-common-kernel-gate"),
    Path("scripts/pi-rpi5-common-kernel-rollout"),
    Path("scripts/pi-rpi5-common-kernel-inspect"),
    Path("scripts/pi-rpi5-common-kernel-identity-status"),
    Path("scripts/rpi5_common_kernel_identity_status.py"),
    Path("scripts/ci/check-rpi5-common-kernel-build.py"),
    Path("scripts/ci/check-rpi5-common-kernel-identity-status.py"),
    Path("scripts/pi_rpi5_common_kernel_test_legacy.py"),
    Path("scripts/test_pi_rpi5_common_kernel.py"),
    Path("scripts/test_pi_rpi5_common_kernel_known_good_fallback.py"),
    Path("scripts/test_pi_rpi5_common_kernel_inspect.py"),
    Path("scripts/test_pi_rpi5_common_kernel_non_generation_paths.py"),
    Path("scripts/test_pi_rpi5_common_kernel_rollout_health.py"),
    Path("scripts/test_pi_rpi5_common_kernel_rollout_selector_resolution.py"),
)

CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "common-kernel-build-contract",
        (sys.executable, "scripts/ci/check-rpi5-common-kernel-build.py"),
    ),
    (
        "common-kernel-identity-status",
        (sys.executable, "scripts/ci/check-rpi5-common-kernel-identity-status.py"),
    ),
    (
        "common-kernel-fixtures",
        (sys.executable, "scripts/test_pi_rpi5_common_kernel.py"),
    ),
    (
        "common-kernel-known-good-fallback",
        (
            sys.executable,
            "scripts/test_pi_rpi5_common_kernel_known_good_fallback.py",
        ),
    ),
    (
        "common-kernel-inspect",
        (sys.executable, "scripts/test_pi_rpi5_common_kernel_inspect.py"),
    ),
    (
        "common-kernel-non-generation-paths",
        (
            sys.executable,
            "scripts/test_pi_rpi5_common_kernel_non_generation_paths.py",
        ),
    ),
    (
        "common-kernel-rollout-health",
        (
            sys.executable,
            "scripts/test_pi_rpi5_common_kernel_rollout_health.py",
        ),
    ),
    (
        "common-kernel-rollout-selector-resolution",
        (
            sys.executable,
            "scripts/test_pi_rpi5_common_kernel_rollout_selector_resolution.py",
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
                reason="infra_common_kernel_source_path_missing",
            )
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return _result(
                status="blocked",
                reason="infra_common_kernel_source_path_invalid",
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
            reason="infra_common_kernel_source_revision_unavailable",
        )
    revision = revision_result.stdout.strip().lower()
    if _GIT_REVISION.fullmatch(revision) is None:
        return _result(
            status="unknown",
            reason="infra_common_kernel_source_revision_invalid",
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
            reason="infra_common_kernel_source_status_unavailable",
            source_revision=revision,
        )
    if status_result.stdout.strip():
        return _result(
            status="blocked",
            reason="infra_common_kernel_repository_dirty",
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
                reason="infra_common_kernel_source_check_unavailable",
                source_revision=revision,
                checks=completed_checks,
            )
        if completed.returncode != 0:
            return _result(
                status="blocked",
                reason="infra_common_kernel_source_check_failed",
                source_revision=revision,
                checks=completed_checks,
            )
        completed_checks += 1

    return _result(
        status="pass",
        reason="infra_common_kernel_source_validated",
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
