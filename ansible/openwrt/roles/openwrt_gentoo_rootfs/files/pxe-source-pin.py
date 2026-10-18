#!/usr/bin/env python3
"""Source-declaration reader for immutable-boot SHA pinning.

Reads /etc/ansible-pull/source-declaration.json and validates its contents
for pinned or legacy boot mode.  No Git, network, subprocess, or write side-
effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Sequence

SOURCE_PATH = Path("/etc/ansible-pull/source-declaration.json")

_SCHEMA = "homecluster-pxe-source-declaration-v1"
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_JSON_MAX = 4096
_ERR_MSG = "source_pin_invalid"

_EXPECTED_KEYS = frozenset(
    ("schema", "environment", "build_id", "infra_revision")
)
_KNOWN_STAGES = frozenset(
    ("stg", "staging", "prod", "prd", "production")
)
_PINNED_STAGES = frozenset(("stg", "staging"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _die(msg: str, /) -> int:
    """Write *msg* to stderr and return exit code 2."""
    sys.stderr.write(msg + "\n")
    return 2


def _raw_decode(text: str) -> dict:
    """Decode JSON text, rejecting duplicate keys via object_pairs_hook."""
    seen: set[str] = set()

    def _hook(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"Duplicate key: {key!r}")
            seen.add(key)
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=_hook)


# ── decode_declaration ───────────────────────────────────────────────────────

def decode_declaration(
    raw: bytes, *, stage: str
) -> dict[str, str]:
    """Validate and decode *raw* declaration bytes.

    *raw* must be 1-4096 bytes, valid UTF-8 JSON object with the exact set of
    keys *schema*, *environment*, *build_id*, *infra_revision*.

    Returns a dict with keys *mode*, *infra_revision*, *build_id*,
    *declaration_sha256*.

    Raises ``ValueError`` on any violation.
    """
    if not isinstance(raw, bytes):
        raise ValueError("raw must be bytes")
    size = len(raw)
    if size == 0 or size > _JSON_MAX:
        raise ValueError(
            f"raw must be 1..{_JSON_MAX} bytes, got {size}"
        )

    # UTF-8 decode (strict)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not valid UTF-8") from exc

    try:
        obj = _raw_decode(text)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    if not isinstance(obj, dict):
        raise ValueError("JSON root must be an object")

    # Exact key match
    if set(obj) != _EXPECTED_KEYS:
        extra = set(obj) - _EXPECTED_KEYS
        missing = _EXPECTED_KEYS - set(obj)
        parts: list[str] = []
        if extra:
            parts.append(f"extra keys: {extra}")
        if missing:
            parts.append(f"missing keys: {missing}")
        raise ValueError("; ".join(parts))

    # scalar type checks
    for k in ("schema", "environment", "build_id", "infra_revision"):
        if not isinstance(obj[k], str):
            raise ValueError(f"{k} must be a string")

    # schema value
    if obj["schema"] != _SCHEMA:
        raise ValueError(
            f"schema must be {_SCHEMA!r}, got {obj['schema']!r}"
        )

    # environment value
    if obj["environment"] != "staging":
        raise ValueError(
            f"environment must be 'staging', got {obj['environment']!r}"
        )

    # stage parameter must be stg or staging
    if stage not in _PINNED_STAGES:
        raise ValueError(
            f"stage must be 'stg' or 'staging', got {stage!r}"
        )

    # build_id
    if not _BUILD_ID_RE.fullmatch(obj["build_id"]):
        raise ValueError(
            f"build_id does not match expected pattern, "
            f"got {obj['build_id']!r}"
        )

    # infra_revision: exactly 40 lowercase hex
    if not _HEX40_RE.fullmatch(obj["infra_revision"]):
        raise ValueError(
            f"infra_revision must be exactly 40 lowercase hex, "
            f"got {obj['infra_revision']!r}"
        )

    return {
        "mode": "pinned",
        "infra_revision": obj["infra_revision"],
        "build_id": obj["build_id"],
        "declaration_sha256": hashlib.sha256(raw).hexdigest(),
    }


# ── resolve ──────────────────────────────────────────────────────────────────

def resolve(*, mode: str, stage: str) -> dict[str, str]:
    """Resolve declaration and return result dict.

    *mode* ∈ {legacy, pinned}, *stage* ∈ {stg, staging, prod, prd, production}.
    Raises ``ValueError`` on any violation.
    """
    if mode not in ("legacy", "pinned"):
        raise ValueError(
            f"mode must be 'legacy' or 'pinned', got {mode!r}"
        )
    if stage not in _KNOWN_STAGES:
        raise ValueError(
            f"stage must be in {sorted(_KNOWN_STAGES)}, got {stage!r}"
        )

    if mode == "legacy":
        # Legacy allowed only when SOURCE_PATH is absent (not a symlink,
        # not a regular file, nothing).
        try:
            st = SOURCE_PATH.lstat()
        except FileNotFoundError:
            return {"mode": "legacy"}
        raise ValueError("SOURCE_PATH exists; cannot use legacy mode")

    # mode == "pinned" – staging required
    if stage not in _PINNED_STAGES:
        raise ValueError("pinned mode requires staging stage")

    # Read file with O_NOFOLLOW | O_NONBLOCK | O_RDONLY
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd: int | None = None
    try:
        fd = os.open(str(SOURCE_PATH), flags)
    except FileNotFoundError:
        raise ValueError("SOURCE_PATH missing for pinned mode")
    except OSError:
        raise ValueError("SOURCE_PATH unavailable")

    try:
        # Guard: must be a regular file (not symlink/FIFO)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("SOURCE_PATH is not a regular file")

        # Read up to 4097 bytes (reject if > 4096)
        payload = os.read(fd, _JSON_MAX + 1)
        if len(payload) > _JSON_MAX:
            raise ValueError(
                f"declaration exceeds {_JSON_MAX} bytes"
            )
        if len(payload) == 0:
            raise ValueError("declaration is empty")

        return decode_declaration(payload, stage=stage)
    finally:
        if fd is not None:
            os.close(fd)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate PXE source declaration"
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--stage", required=True)
    args = parser.parse_args(argv)

    try:
        result = resolve(mode=args.mode, stage=args.stage)
    except (ValueError, OSError):
        return _die(_ERR_MSG)

    if result["mode"] == "legacy":
        print("legacy - - -")
        return 0

    print(
        " ".join(
            (
                result["mode"],
                result["infra_revision"],
                result["build_id"],
                result["declaration_sha256"],
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
