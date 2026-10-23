#!/usr/bin/env python3
"""Read four inventory-bound staging PXE selectors on OpenWrt without mutation.

The caller supplies four validated board hashes in fixed slot order. This
probe accepts no host, path, or arbitrary command and does not print hashes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence

SCHEMA = "pxe-live-selectors-readonly-v1"
ROOT = Path("/srv/gentoo/tftp-root")
SLOTS = ("server", "agent-01", "agent-02", "agent-03")
_HASH = re.compile(r"^[0-9a-fA-F]{8}$")
_RELEASE = re.compile(r"^[0-9]{8}-rpi[45]$")
_ROOT_ARG = re.compile(r"(?:^|\s)root=nfs4:[^\s]*:/gentoo/([0-9]{8}-rpi[45])/nfs(?:,|\s|$)")


def _unknown(reason: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "status": "unknown", "reason": reason,
            "scope": "current_openwrt_selector_not_boot_attestation", "selectors": []}


def _read_one(root: Path, board_hash: str) -> tuple[str, str] | None:
    host = root / board_hash
    try:
        if host.is_symlink():
            link = os.readlink(host)
            match = re.fullmatch(r"dates/([0-9]{8}-rpi[45])", link)
            if match is None:
                return None
            tftp = match.group(1)
            cmdline = root / "dates" / tftp / "cmdline.txt"
        elif host.is_dir():
            link = os.readlink(host / "kernel8.img")
            match = re.fullmatch(r"\.\./dates/([0-9]{8}-rpi[45])/kernel8\.img", link)
            if match is None:
                return None
            tftp = match.group(1)
            cmdline = host / "cmdline.txt"
        else:
            return None
        if cmdline.is_symlink() or not cmdline.is_file() or cmdline.stat().st_size > 4096:
            return None
        line = cmdline.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    roots = _ROOT_ARG.findall(line)
    if len(roots) != 1 or _RELEASE.fullmatch(tftp) is None:
        return None
    return tftp, roots[0]


def inspect(board_hashes: Sequence[str], *, root: Path = ROOT) -> dict[str, Any]:
    if (len(board_hashes) != 4
        or any(not isinstance(h, str) or _HASH.fullmatch(h) is None for h in board_hashes)
        or len(set(h.lower() for h in board_hashes)) != 4):
        return _unknown("board_hashes_invalid")
    selectors = []
    for slot, board_hash in zip(SLOTS, board_hashes):
        observed = _read_one(root, board_hash)
        if observed is None:
            return _unknown("selector_unavailable_or_invalid")
        tftp, rootfs = observed
        if tftp[-4:] != rootfs[-4:]:
            return _unknown("selector_architecture_mismatch")
        selectors.append({"slot": slot, "tftp_release": tftp,
                          "rootfs_release": rootfs})
    return {"schema": SCHEMA, "status": "partial",
            "reason": "selectors_observed_boot_use_unattested",
            "scope": "current_openwrt_selector_not_boot_attestation",
            "selectors": selectors}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed read-only staging PXE selectors")
    parser.add_argument("--json", required=True, action="store_true")
    parser.add_argument("--board-hashes", required=True)
    args = parser.parse_args(argv)
    result = inspect(args.board_hashes.split(","))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
