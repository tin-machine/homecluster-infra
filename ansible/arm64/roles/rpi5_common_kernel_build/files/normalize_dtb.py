#!/usr/bin/env python3
"""Normalize the Pi5 boot DTB before calculating the immutable artifact hash."""

from __future__ import annotations

import sys
from pathlib import Path


NEEDLE = b"cgroup_disable=memory"
FDT_MAGIC = bytes.fromhex("d00dfeed")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: normalize_dtb.py <built-dtb>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if path.is_symlink() or not path.is_file():
        print("built_dtb_invalid", file=sys.stderr)
        return 2
    data = path.read_bytes()
    if not data.startswith(FDT_MAGIC) or data.count(NEEDLE) > 1:
        print("built_dtb_contract_invalid", file=sys.stderr)
        return 2
    if NEEDLE not in data:
        print("status=unchanged")
        return 0
    path.write_bytes(data.replace(NEEDLE, b" " * len(NEEDLE)))
    print("status=patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
