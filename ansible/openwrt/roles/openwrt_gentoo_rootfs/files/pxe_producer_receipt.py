#!/usr/bin/env python3
"""Measure completed paired artifacts and publish an immutable producer receipt.

Only the fixed build directory is addressable from the CLI. Private execution
inputs are deliberately absent: the controller must supply independent evidence
of its checkout and inventory use. Content hashes are not issuer authentication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from pxe_artifact_fingerprint import fingerprint_tree
from pxe_producer_receipt_contract import (
    SCHEMA, GENERATIONS, MAX_RECEIPT, MAX_TFTP_FILE,
    _string, _date, _object, _json, _canonical, _receipt_id,
    _directory, _file, _required_path, _validate_window, validate_receipt,
)

ROOT = Path("/srv/gentoo")
MANIFEST_KEYS = {"schema", "policy", "bundle_date", "releases", "target_hosts",
                 "target_stages", "required_tftp_paths", "markers", "metadata"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifest(raw, date):
    value = _object(_json(raw), MANIFEST_KEYS)
    expected = {generation: f"{date}-{generation}" for generation in GENERATIONS}
    if (type(value["schema"]) is not int or value["schema"] != 1
            or value["bundle_date"] != date or value["policy"] != "paired_clean"
            or value["releases"] != expected or type(value["metadata"]) is not dict
            or any(type(value[key]) is not list for key in ("target_hosts", "target_stages", "markers"))):
        raise ValueError("paired manifest mismatch")
    if not value["target_stages"] or any(stage not in ("stg", "staging") for stage in value["target_stages"]):
        raise ValueError("paired manifest is not staging scoped")
    required = value["required_tftp_paths"]
    if type(required) is not list or not 1 <= len(required) <= 128:
        raise ValueError("required artifact list invalid")
    paths = {generation: [] for generation in GENERATIONS}
    for item in required:
        _object(item, {"generation", "release", "path"})
        generation = _string(item["generation"], r"rpi[45]")
        if item["release"] != expected[generation]:
            raise ValueError("required artifact release mismatch")
        path = _required_path(item["path"])
        if path in paths[generation]:
            raise ValueError("duplicate required artifact")
        paths[generation].append(path)
    for generation, names in paths.items():
        dtb = "bcm2711-rpi-4-b.dtb" if generation == "rpi4" else "bcm2712-rpi-5-b.dtb"
        if (dtb not in names or "overlays" not in names
                or not any(re.fullmatch(r"kernel[A-Za-z0-9._-]*\.img", name) for name in names)
                or not any(re.fullmatch(r"initramfs[A-Za-z0-9._-]*\.img", name) for name in names)):
            raise ValueError("required boot artifacts incomplete")
    return {generation: sorted(names) for generation, names in paths.items()}


def _declaration(raw, build_id, revision):
    value = _object(_json(raw), {"schema", "environment", "build_id", "infra_revision"})
    if value != {"schema": "homecluster-pxe-source-declaration-v1", "environment": "staging",
                 "build_id": build_id, "infra_revision": revision}:
        raise ValueError("source declaration mismatch")
    return hashlib.sha256(raw).hexdigest()


def _required_files(root, names):
    result = []
    for name in names:
        path = root / name
        if name == "overlays":
            with _directory(path):
                pass
            result.append({"path": name, "type": "directory"})
        else:
            digest, size = _file(path, MAX_TFTP_FILE, digest_only=True)
            result.append({"path": name, "type": "regular", "sha256": digest, "size": size})
    return result


def _publish(path, value):
    raw = _canonical(value) + b"\n"
    if len(raw) > MAX_RECEIPT:
        raise ValueError("producer receipt too large")
    with _directory(path.parent) as parent:
        temporary = ".producer-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            # Hard-link publication is atomic and cannot overwrite an old receipt.
            os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
    return raw


def produce(*, bundle_date, build_id, infra_revision, generation_profile):
    _date(bundle_date)
    _string(build_id, r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    _string(infra_revision, r"[0-9a-f]{40}")
    _string(generation_profile, r"standard|rpi5-common-kernel")
    manifest_path = ROOT / "releases" / (bundle_date + ".json")
    destination = ROOT / "releases/producer" / (bundle_date + ".json")
    started = _now()
    manifest_raw = _file(manifest_path, 512 * 1024)
    paths = _manifest(manifest_raw, bundle_date)
    artifacts, declarations, measurement = {}, {}, {}
    for generation in GENERATIONS:
        release = f"{bundle_date}-{generation}"
        rootfs = ROOT / release / "nfs"
        tftp = ROOT / "tftp-root/dates" / release
        declaration = rootfs / "etc/ansible-pull/source-declaration.json"
        declarations[generation] = _declaration(_file(declaration, 4096), build_id, infra_revision)
        required = _required_files(tftp, paths[generation])
        rootfs_timing, tftp_timing = {}, {}
        artifacts[generation] = {"rootfs": fingerprint_tree(rootfs, timing=rootfs_timing),
                                 "tftp": fingerprint_tree(tftp, timing=tftp_timing),
                                 "required_tftp": required}
        measurement[generation] = {"rootfs": rootfs_timing, "tftp": tftp_timing}
        if required != _required_files(tftp, paths[generation]):
            raise ValueError("required artifacts changed during measurement")
    if len(set(declarations.values())) != 1:
        raise ValueError("paired declarations differ")
    for generation in GENERATIONS:
        declaration = ROOT / f"{bundle_date}-{generation}" / "nfs/etc/ansible-pull/source-declaration.json"
        if _declaration(_file(declaration, 4096), build_id, infra_revision) != declarations[generation]:
            raise ValueError("source declaration changed during measurement")
    if _file(manifest_path, 512 * 1024) != manifest_raw:
        raise ValueError("paired manifest changed during measurement")
    value = {"schema": SCHEMA, "scope": "measured_build_artifacts", "environment": "staging",
             "bundle_date": bundle_date, "build_id": build_id, "generation_profile": generation_profile,
             "infra_generation_revision": infra_revision,
             "artifact_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
             "source_declarations": declarations, "artifacts": artifacts,
             "capture_window": {"started_at": started, "finished_at": _now()}}
    _validate_window(value["capture_window"])
    value["receipt_id"] = _receipt_id(value)
    validate_receipt(value)
    status = "written"
    try:
        existing_raw = _file(destination, MAX_RECEIPT)
    except FileNotFoundError:
        existing_raw = None
    if existing_raw is not None:
        existing = validate_receipt(_json(existing_raw))
        expected = {**value, "capture_window": existing["capture_window"]}
        expected["receipt_id"] = _receipt_id(expected)
        if existing_raw != _canonical(expected) + b"\n":
            raise ValueError("existing producer receipt conflicts")
        raw, value, status = existing_raw, existing, "unchanged"
    else:
        raw = _publish(destination, value)
    return {"status": status, "bundle_date": bundle_date, "receipt_id": value["receipt_id"],
            "receipt_sha256": hashlib.sha256(raw).hexdigest(), "measurement": measurement}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-date", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--infra-revision", required=True)
    parser.add_argument("--generation-profile", required=True)
    args = parser.parse_args()
    try:
        result = produce(**vars(args))
    except (ValueError, OSError, TypeError, RecursionError):
        print('{"status":"blocked","reason":"producer_receipt_failed"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
