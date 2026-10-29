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
LEGACY_LABEL = re.compile(r"^[0-9]{8}-rpi5-nvidia$")
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


def linked_build_labels(root, modules_root=Path("/lib/modules")):
    """Protect even dangling module build links; these can be needed by DKMS."""
    labels = set()
    if not modules_root.is_dir():
        raise CleanupBlocked("modules_root_missing")
    for base, dirs, files in os.walk(modules_root, followlinks=False):
        for name in dirs + files:
            link = Path(base) / name
            if not link.is_symlink():
                continue
            target = link.resolve(strict=False)
            try:
                relative = target.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                labels.add(relative.parts[0])
    return labels


def mounted_below(paths, mountinfo=Path("/proc/self/mountinfo")):
    """Never recurse through a mount inside a selected work directory."""
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        if len(fields) < 5:
            raise CleanupBlocked("mountinfo_invalid")
        mount = Path(fields[4].replace("\\040", " "))
        for path in paths:
            if mount == path or path in mount.parents:
                return True
    return False


def separate_work_mount(root):
    return root.stat().st_dev != Path("/").stat().st_dev


def build_plan(root, protected, min_free_bytes, free_bytes, proc_root=Path("/proc"),
               post_complete=False, modules_root=Path("/lib/modules"),
               mountinfo=Path("/proc/self/mountinfo")):
    if root.is_symlink() or not root.is_dir():
        raise CleanupBlocked("invalid_work_root")
    protected = set(protected)
    if post_complete:
        protected.update(linked_build_labels(root, modules_root))
    eligible = []
    for path in root.iterdir():
        if not LABEL.fullmatch(path.name) and not (post_complete and LEGACY_LABEL.fullmatch(path.name)):
            continue
        mode = path.lstat().st_mode
        if not stat.S_ISDIR(mode):
            raise CleanupBlocked("dated_entry_not_directory")
        if path.name in protected:
            continue
        # A work directory newer than the oldest protected generation is not stale.
        if not post_complete and path.name >= min(protected):
            continue
        eligible.append(path)
    eligible.sort(key=lambda path: path.name)
    if post_complete and mounted_below(eligible, mountinfo):
        raise CleanupBlocked("candidate_contains_mount")
    if active_references(eligible, proc_root):
        raise CleanupBlocked("active_build_reference")
    selected = []
    projected_free = free_bytes
    for path in eligible:
        if not post_complete and projected_free >= min_free_bytes:
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
        "post_complete": post_complete,
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
    parser.add_argument("--candidate", default="")
    parser.add_argument("--accepted", required=True)
    parser.add_argument("--rollback", default="")
    parser.add_argument("--min-free-gib", type=int, default=48)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256", default="")
    parser.add_argument("--post-complete", action="store_true")
    parser.add_argument("--protected-label", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.post_complete:
            if not LABEL.fullmatch(args.accepted) or not args.protected_label:
                raise CleanupBlocked("post_complete_protection_missing")
            if not separate_work_mount(args.root):
                raise CleanupBlocked("work_root_not_separate_mount")
            if any(not (LABEL.fullmatch(label) or LEGACY_LABEL.fullmatch(label))
                   for label in args.protected_label):
                raise CleanupBlocked("invalid_protected_label")
            protected = {args.accepted, *args.protected_label}
            accepted_path = args.root / args.accepted
            if accepted_path.is_symlink() or not accepted_path.is_dir():
                raise CleanupBlocked("accepted_build_missing")
            if args.accepted not in linked_build_labels(args.root):
                raise CleanupBlocked("accepted_build_link_missing")
        else:
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
        plan = build_plan(args.root, protected, args.min_free_gib * 1024**3, free_bytes,
                          post_complete=args.post_complete)
        if args.apply:
            if plan["plan_sha256"] != args.expected_plan_sha256:
                raise CleanupBlocked("plan_changed")
            for label in plan["delete_labels"]:
                path = args.root / label
                if path.is_symlink() or not path.is_dir() or active_references([path]) or (
                    args.post_complete and (
                        mounted_below([path]) or path.name in linked_build_labels(args.root)
                    )
                ):
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
