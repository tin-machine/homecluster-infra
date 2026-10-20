#!/usr/bin/env python3
"""Shared producer receipt validation and fixed, read-only stored-receipt probe.

Hashes identify historical measurements; they do not prove current artifacts,
issuer authentication, execution success, or cluster acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path("/srv/gentoo")

SCHEMA = "homecluster-pxe-producer-receipt-v1"
DOMAIN = b"homecluster-pxe-producer-receipt-v1\x00"
GENERATIONS = ("rpi4", "rpi5")
RECEIPT_KEYS = {"schema", "scope", "environment", "build_id", "generation_profile",
                "bundle_date", "infra_generation_revision", "artifact_manifest_sha256",
                "source_declarations", "artifacts", "capture_window", "receipt_id"}
MAX_RECEIPT = 256 * 1024
MAX_TFTP_FILE = 2 * 1024 * 1024 * 1024


def _string(value, pattern):
    if type(value) is not str or re.fullmatch(pattern, value, flags=re.ASCII) is None:
        raise ValueError("invalid producer identity")
    return value


def _date(value):
    _string(value, r"[0-9]{8}")
    datetime.strptime(value, "%Y%m%d")
    return value


def _timestamp(value):
    _string(value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def _object(value, keys):
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid producer object")
    return value


def _json(raw):
    def reject_constant(_value):
        raise ValueError("invalid JSON constant")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _receipt_id(value):
    payload = {key: item for key, item in value.items() if key != "receipt_id"}
    return "sha256:" + hashlib.sha256(DOMAIN + _canonical(payload)).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_uid,
            info.st_gid, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _directory(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("invalid producer path")
    descriptors, bindings = [], []
    try:
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for part in path.parts[1:]:
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(child)
            if _identity(before) != _identity(os.fstat(child)):
                raise ValueError("producer directory changed")
            bindings.append((current, part, before))
            current = child
        yield current
        for parent, name, before in bindings:
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_mode) != (after.st_dev, after.st_ino, after.st_mode):
                raise ValueError("producer directory binding changed")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _file(path, limit, *, digest_only=False):
    with _directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise ValueError("invalid producer input file")
            digest, chunks, size = hashlib.sha256(), [], 0
            while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - size)):
                size += len(chunk)
                if size > limit:
                    raise ValueError("producer input exceeds budget")
                digest.update(chunk)
                if not digest_only:
                    chunks.append(chunk)
            if (size != before.st_size or _identity(before) != _identity(os.fstat(descriptor))
                    or _identity(before) != _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("producer input changed")
            return (digest.hexdigest(), size) if digest_only else b"".join(chunks)
        finally:
            os.close(descriptor)


def _required_path(value):
    _string(value, r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("unsafe required artifact path")
    return value


def _validate_window(value):
    window = _object(value, {"started_at", "finished_at"})
    if _timestamp(window["started_at"]) > _timestamp(window["finished_at"]):
        raise ValueError("producer clock moved backwards")


def validate_receipt(value):
    """Strict semantic validation, including rehashed malformed receipts."""
    _object(value, RECEIPT_KEYS)
    if (value["schema"], value["scope"], value["environment"]) != (SCHEMA, "measured_build_artifacts", "staging"):
        raise ValueError("invalid producer receipt scope")
    _date(value["bundle_date"])
    _string(value["build_id"], r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    _string(value["generation_profile"], r"standard|rpi5-common-kernel")
    _string(value["infra_generation_revision"], r"[0-9a-f]{40}")
    _string(value["artifact_manifest_sha256"], r"[0-9a-f]{64}")
    _object(value["source_declarations"], set(GENERATIONS))
    for digest in value["source_declarations"].values():
        _string(digest, r"[0-9a-f]{64}")
    if len(set(value["source_declarations"].values())) != 1:
        raise ValueError("paired producer declarations differ")
    _validate_window(value["capture_window"])
    _object(value["artifacts"], set(GENERATIONS))
    count = 0
    for generation, artifact in value["artifacts"].items():
        _object(artifact, {"rootfs", "tftp", "required_tftp"})
        for name in ("rootfs", "tftp"):
            tree = _object(artifact[name], {"schema", "algorithm", "digest", "entries", "regular_file_bytes"})
            if (tree["schema"], tree["algorithm"]) != ("homecluster-pxe-artifact-tree-v1", "sha256-posix-tree-v1"):
                raise ValueError("unsupported producer artifact digest")
            _string(tree["digest"], r"[0-9a-f]{64}")
            if (type(tree["entries"]) is not int or not 1 <= tree["entries"] <= 1_000_000
                    or type(tree["regular_file_bytes"]) is not int or not 0 <= tree["regular_file_bytes"] <= 1024 ** 4):
                raise ValueError("invalid producer artifact counts")
        required = artifact["required_tftp"]
        if type(required) is not list or not 1 <= len(required) <= 128:
            raise ValueError("invalid required producer artifacts")
        names = []
        for item in required:
            if type(item) is not dict:
                raise ValueError("invalid required producer artifact")
            name = _required_path(item.get("path"))
            names.append(name)
            if name == "overlays":
                _object(item, {"path", "type"})
                if item["type"] != "directory":
                    raise ValueError("invalid producer overlays")
            else:
                _object(item, {"path", "type", "sha256", "size"})
                if item["type"] != "regular" or type(item["size"]) is not int or not 0 < item["size"] <= MAX_TFTP_FILE:
                    raise ValueError("invalid required producer file")
                _string(item["sha256"], r"[0-9a-f]{64}")
        if names != sorted(set(names)):
            raise ValueError("noncanonical required producer artifacts")
        dtb = "bcm2711-rpi-4-b.dtb" if generation == "rpi4" else "bcm2712-rpi-5-b.dtb"
        if (dtb not in names or "overlays" not in names
                or not any(re.fullmatch(r"kernel[A-Za-z0-9._-]*\.img", name) for name in names)
                or not any(re.fullmatch(r"initramfs[A-Za-z0-9._-]*\.img", name) for name in names)):
            raise ValueError("required producer boot artifacts incomplete")
        count += len(required)
    if count > 128 or value["receipt_id"] != _receipt_id(value):
        raise ValueError("invalid producer receipt identity")
    return value


def read_receipt(bundle_date):
    """Read only the fixed completed receipt; never scan/re-hash artifact trees."""
    _date(bundle_date)
    path = ROOT / "releases/producer" / (bundle_date + ".json")
    raw = _file(path, MAX_RECEIPT)
    value = validate_receipt(_json(raw))
    if value["bundle_date"] != bundle_date or raw != _canonical(value) + b"\n":
        raise ValueError("producer receipt date or bytes mismatch")
    return value, hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-date", required=True)
    args = parser.parse_args()
    result = {"schema": "pxe-producer-receipt-readonly-observation-v1", "status": "unknown",
              "reason": "producer_receipt_invalid", "scope": "stored_producer_receipt_not_current_artifact_verification",
              "receipt": None, "receipt_sha256": None, "capture_started_at": None,
              "capture_finished_at": None, "observed_at": None}
    try:
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        receipt, digest = read_receipt(args.bundle_date)
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not receipt["capture_window"]["finished_at"] <= started <= finished:
            raise ValueError("producer observation clock invalid")
        result.update(status="pass", reason="producer_receipt_observed", receipt=receipt, receipt_sha256=digest,
                      capture_started_at=started, capture_finished_at=finished, observed_at=finished)
    except FileNotFoundError:
        result["reason"] = "producer_receipt_missing"
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
