#!/usr/bin/env python3
"""Normalize a Pi boot DTB before artifact acceptance, or check it read-only."""

from __future__ import annotations

import sys
from pathlib import Path


NEEDLE = b"cgroup_disable=memory"
FDT_MAGIC = bytes.fromhex("d00dfeed")


def main() -> int:
    args = sys.argv[1:]
    check_only = bool(args and args[0] == "--check")
    if check_only:
        args = args[1:]
    if len(args) != 1:
        print("usage: normalize_dtb.py [--check] <dtb>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if path.is_symlink() or not path.is_file():
        print("dtb_invalid", file=sys.stderr)
        return 2
    data = path.read_bytes()
    if not data.startswith(FDT_MAGIC) or data.count(NEEDLE) > 1:
        print("dtb_contract_invalid", file=sys.stderr)
        return 2
    if NEEDLE not in data:
        print("status=unchanged")
        return 0
    if check_only:
        print("dtb_not_normalized", file=sys.stderr)
        return 1
    path.write_bytes(data.replace(NEEDLE, b" " * len(NEEDLE)))
    print("status=patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
