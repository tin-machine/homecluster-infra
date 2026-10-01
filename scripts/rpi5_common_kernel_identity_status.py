#!/usr/bin/env python3
"""Read-only collision probe for common-kernel generation identities.

The two identities are intentionally independent:
- kernel_build_date selects the Pi5 kernel/NVIDIA build namespace.
- pxe_release_date selects the paired PXE release namespace.

The probe reads generated inventory and fixed remote paths only. It never
mutates inventory, builder state, OpenWrt state, or generation state.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import pxe_release_identity_status as pxe_identity


BUILDER_GROUP = "rpi5_egpu_artifact_bundle"
BUILD_DATE_KEY = "rpi5_common_kernel_build_stage_date"
BUILD_RELEASE_OVERRIDE_KEY = "rpi5_common_kernel_build_release_override"
BUILD_WORK_ROOT_KEY = "rpi5_common_kernel_build_work_root"
BUNDLE_OUTPUT_DIR_KEY = "rpi5_egpu_nvidia_artifact_bundle_output_dir"
DEFAULT_BUILD_WORK_ROOT = "/var/lib/rancher/k3s/kernel-build"
DEFAULT_BUNDLE_OUTPUT_DIR = "/var/lib/rancher/k3s/nvidia-artifacts"
UNSUPPORTED_PATH_OVERRIDE_KEYS = (
    "rpi5_common_kernel_build_label",
    "rpi5_common_kernel_build_dir",
    "rpi5_common_kernel_build_metadata_path",
    "rpi5_egpu_nvidia_artifact_bundle_archive_name",
    "rpi5_egpu_nvidia_artifact_bundle_archive_path",
    "rpi5_egpu_nvidia_artifact_bundle_manifest_path",
)
MARKER = "COMMON_KERNEL_IDENTITY_COLLISION"
_DATE_RE = re.compile(r"^[0-9]{8}$")
_MARKER_RE = re.compile(r"COMMON_KERNEL_IDENTITY_COLLISION=([01])")
_TIMEOUT_SECONDS = 30

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]

_REASONS = {
    "identities_available",
    "kernel_identity_in_use",
    "pxe_identity_in_use",
    "probe_blocked",
    "source_unavailable",
}
_COMPONENT = {"available", "in_use", "not_checked", "unknown"}


def terminal(
    status: str,
    reason: str,
    kernel_build_date: str,
    pxe_release_date: str,
    *,
    kernel_identity: str,
    pxe_identity: str,
) -> dict[str, str]:
    if status not in {"pass", "blocked", "unknown"}:
        raise ValueError("status_invalid")
    if reason not in _REASONS:
        raise ValueError("reason_invalid")
    if kernel_identity not in _COMPONENT or pxe_identity not in _COMPONENT:
        raise ValueError("component_status_invalid")
    return {
        "status": status,
        "reason": reason,
        "kernel_build_date": kernel_build_date,
        "pxe_release_date": pxe_release_date,
        "kernel_identity": kernel_identity,
        "pxe_identity": pxe_identity,
    }


def probe_candidates(
    kernel_build_date: str,
    pxe_release_date: str,
    *,
    inventory_path: Path,
    runner: Runner | None = None,
) -> dict[str, str]:
    if not _date_valid(kernel_build_date) or not _date_valid(pxe_release_date):
        return terminal(
            "blocked",
            "probe_blocked",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="unknown",
            pxe_identity="unknown",
        )

    invoke = _run if runner is None else runner

    builder_contract = _builder_contract(
        kernel_build_date,
        inventory_path=inventory_path,
        runner=invoke,
    )
    if builder_contract is None:
        return terminal(
            "unknown",
            "source_unavailable",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="unknown",
            pxe_identity="not_checked",
        )
    if builder_contract["status"] == "blocked":
        return terminal(
            "blocked",
            "probe_blocked",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="unknown",
            pxe_identity="not_checked",
        )
    current_kernel = str(builder_contract["current_kernel"])
    if current_kernel == kernel_build_date:
        return terminal(
            "blocked",
            "kernel_identity_in_use",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="in_use",
            pxe_identity="not_checked",
        )

    kernel_collision = _builder_collision(
        tuple(builder_contract["collision_paths"]),
        inventory_path=inventory_path,
        runner=invoke,
    )
    if kernel_collision is None:
        return terminal(
            "unknown",
            "source_unavailable",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="unknown",
            pxe_identity="not_checked",
        )
    if kernel_collision:
        return terminal(
            "blocked",
            "kernel_identity_in_use",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="in_use",
            pxe_identity="not_checked",
        )

    pxe = pxe_identity.probe_candidate(
        pxe_release_date,
        inventory_path=inventory_path,
        runner=invoke,
    )
    if pxe["status"] == "unknown":
        return terminal(
            "unknown",
            "source_unavailable",
            kernel_build_date,
            pxe_release_date,
            kernel_identity="available",
            pxe_identity="unknown",
        )
    if pxe["status"] == "blocked":
        return terminal(
            "blocked",
            (
                "pxe_identity_in_use"
                if pxe["reason"] == "identity_in_use"
                else "probe_blocked"
            ),
            kernel_build_date,
            pxe_release_date,
            kernel_identity="available",
            pxe_identity=(
                "in_use"
                if pxe["reason"] == "identity_in_use"
                else "unknown"
            ),
        )

    return terminal(
        "pass",
        "identities_available",
        kernel_build_date,
        pxe_release_date,
        kernel_identity="available",
        pxe_identity="available",
    )


def _builder_contract(
    candidate: str,
    *,
    inventory_path: Path,
    runner: Runner,
) -> dict[str, object] | None:
    completed = runner(
        (
            "ansible-inventory",
            "-i",
            str(inventory_path),
            "--list",
        ),
        _TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        return None
    inventory = _json_object(completed.stdout)
    if inventory is None:
        return None

    group = inventory.get(BUILDER_GROUP)
    hosts = group.get("hosts") if isinstance(group, dict) else None
    if (
        not isinstance(hosts, list)
        or len(hosts) != 1
        or not isinstance(hosts[0], str)
        or not hosts[0]
    ):
        return None
    host = hosts[0]

    meta = inventory.get("_meta")
    hostvars = meta.get("hostvars") if isinstance(meta, dict) else None
    values = hostvars.get(host) if isinstance(hostvars, dict) else None
    if not isinstance(values, dict):
        return None

    current = values.get(BUILD_DATE_KEY)
    if type(current) not in {str, int}:
        return None
    current_kernel = str(current)
    if not _date_valid(current_kernel):
        return None

    release_override = values.get(BUILD_RELEASE_OVERRIDE_KEY, "")
    if release_override not in {"", None}:
        return {"status": "blocked"}

    for key in UNSUPPORTED_PATH_OVERRIDE_KEYS:
        if key in values and values.get(key) not in {"", None}:
            return {"status": "blocked"}

    build_work_root = values.get(
        BUILD_WORK_ROOT_KEY,
        DEFAULT_BUILD_WORK_ROOT,
    )
    bundle_output_dir = values.get(
        BUNDLE_OUTPUT_DIR_KEY,
        DEFAULT_BUNDLE_OUTPUT_DIR,
    )
    if not _safe_runtime_root(build_work_root) or not _safe_runtime_root(
        bundle_output_dir
    ):
        return {"status": "blocked"}

    release = f"{candidate}-rpi5"
    label = f"{release}-homecluster"
    archive = f"rpi5-egpu-{release}.tar.gz"
    return {
        "status": "pass",
        "current_kernel": current_kernel,
        "collision_paths": (
            str(Path(str(build_work_root)) / label),
            str(Path(str(bundle_output_dir)) / archive),
            str(Path(str(bundle_output_dir)) / f"{archive}.sha256"),
        ),
    }


def _safe_runtime_root(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("/var/lib/rancher/k3s/"):
        return False
    path = Path(value)
    if ".." in path.parts or not path.is_absolute():
        return False
    return "{{" not in value and "}}" not in value

def _builder_collision(
    paths: tuple[str, ...],
    *,
    inventory_path: Path,
    runner: Runner,
) -> bool | None:
    completed = runner(
        (
            "ansible",
            "-i",
            str(inventory_path),
            BUILDER_GROUP,
            "-m",
            "raw",
            "-a",
            _builder_probe_command(paths),
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


def _builder_probe_command(paths: tuple[str, ...]) -> str:
    if (
        len(paths) != 3
        or any(not _safe_runtime_path(path) for path in paths)
    ):
        raise ValueError("path_invalid")
    quoted_paths = " ".join(f"'{path}'" for path in paths)
    return (
        "set -eu; "
        "collision=0; "
        f"for path in {quoted_paths}; do "
        'if [ -e "$path" ] || [ -L "$path" ]; then collision=1; fi; '
        "done; "
        f'printf "{MARKER}=%s\\n" "$collision"'
    )


def _safe_runtime_path(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("/var/lib/rancher/k3s/"):
        return False
    path = Path(value)
    return (
        path.is_absolute()
        and ".." not in path.parts
        and "{{" not in value
        and "}}" not in value
    )

def _date_valid(value: object) -> bool:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
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


def _run(
    command: Sequence[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
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
        description=(
            "Check whether common-kernel build and paired PXE release "
            "identities are both unused."
        )
    )
    value.add_argument("--kernel-build-date", required=True)
    value.add_argument("--pxe-release-date", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    result = probe_candidates(
        args.kernel_build_date,
        args.pxe_release_date,
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
