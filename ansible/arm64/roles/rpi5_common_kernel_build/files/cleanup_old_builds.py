#!/usr/bin/env python3
"""Plan or remove obsolete, unreferenced Pi5 kernel build work directories."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys


LABEL = re.compile(r"^[0-9]{8}-rpi5-homecluster$")
PRODUCTION_ROOT = Path("/var/lib/rancher/k3s/kernel-build")


class CleanupBlocked(Exception):
    pass


def validate_labels(candidate, accepted, rollback):
    labels = (candidate, accepted, rollback)
    if any(not LABEL.fullmatch(label) for label in labels):
        raise CleanupBlocked("invalid_protected_label")
    if len(set(labels)) != 3:
        raise CleanupBlocked("protected_labels_not_distinct")
    return set(labels)


def directory_bytes(path):
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            entry = Path(base) / name
            total += entry.lstat().st_blocks * 512
    return total + path.lstat().st_blocks * 512


def active_references(paths, proc_root=Path("/proc")):
    prefixes = tuple(str(path) + "/" for path in paths)
    exact = {str(path) for path in paths}
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        references = [proc / "cwd", proc / "exe"]
        try:
            references.extend((proc / "fd").iterdir())
        except (OSError, PermissionError):
            pass
        for reference in references:
            try:
                target = os.readlink(reference).removesuffix(" (deleted)")
            except (OSError, PermissionError):
                continue
            if target in exact or target.startswith(prefixes):
                return True
    return False


def build_plan(root, protected, min_free_bytes, free_bytes, proc_root=Path("/proc")):
    if root.is_symlink() or not root.is_dir():
        raise CleanupBlocked("invalid_work_root")
    eligible = []
    for path in root.iterdir():
        if not LABEL.fullmatch(path.name):
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise CleanupBlocked("dated_entry_not_directory")
        if path.name in protected:
            continue
        # A work directory newer than the oldest protected generation is not stale.
        if path.name >= min(protected):
            continue
        eligible.append(path)
    eligible.sort(key=lambda path: path.name)
    if active_references(eligible, proc_root):
        raise CleanupBlocked("active_build_reference")
    selected = []
    projected_free = free_bytes
    for path in eligible:
        if projected_free >= min_free_bytes:
            break
        selected.append(path)
        projected_free += directory_bytes(path)
    if projected_free < min_free_bytes:
        raise CleanupBlocked("insufficient_reclaimable_space")
    identity = {
        "root": str(root),
        "protected": sorted(protected),
        "delete": [{"label": path.name, "inode": path.lstat().st_ino} for path in selected],
        "min_free_bytes": min_free_bytes,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "status": "pass",
        "plan_sha256": digest,
        "delete_labels": [path.name for path in selected],
        "protected_labels": sorted(protected),
        "free_bytes_before": free_bytes,
        "projected_free_bytes": projected_free,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--accepted", required=True)
    parser.add_argument("--rollback", required=True)
    parser.add_argument("--min-free-gib", type=int, default=48)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256", default="")
    args = parser.parse_args()
    try:
        protected = validate_labels(args.candidate, args.accepted, args.rollback)
        if args.root != PRODUCTION_ROOT or args.root.is_symlink():
            raise CleanupBlocked("unexpected_work_root")
        if not 1 <= args.min_free_gib <= 200:
            raise CleanupBlocked("invalid_min_free_gib")
        if args.apply and not re.fullmatch(r"[0-9a-f]{64}", args.expected_plan_sha256):
            raise CleanupBlocked("plan_digest_required")
        if not args.apply and args.expected_plan_sha256:
            raise CleanupBlocked("plan_digest_without_apply")
        free_bytes = shutil.disk_usage(args.root).free
        plan = build_plan(args.root, protected, args.min_free_gib * 1024**3, free_bytes)
        if args.apply:
            if plan["plan_sha256"] != args.expected_plan_sha256:
                raise CleanupBlocked("plan_changed")
            for label in plan["delete_labels"]:
                path = args.root / label
                if path.is_symlink() or not path.is_dir() or active_references([path]):
                    raise CleanupBlocked("candidate_changed_or_active")
                shutil.rmtree(path)
            plan["free_bytes_after"] = shutil.disk_usage(args.root).free
            if plan["free_bytes_after"] < args.min_free_gib * 1024**3:
                raise CleanupBlocked("free_space_goal_not_met")
        plan["mode"] = "apply" if args.apply else "preview"
        print(json.dumps(plan, sort_keys=True))
    except (CleanupBlocked, OSError) as exc:
        reason = str(exc) if isinstance(exc, CleanupBlocked) else "filesystem_error"
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
