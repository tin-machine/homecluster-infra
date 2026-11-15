#!/usr/bin/env python3
"""Update mutable Gentoo hosts; leave pinned PXE releases to generation builds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


WORLD_FILE = Path("/var/lib/portage/world")
SOURCE_DECLARATION = Path("/etc/ansible-pull/source-declaration.json")
REQUIRED_ATOMS = (
    "app-admin/ansible-core", "dev-vcs/git", "net-firewall/iptables",
    "net-firewall/nftables", "sys-fs/f2fs-tools",
)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source declaration field")
        result[key] = value
    return result


def is_pinned(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4096:
        raise ValueError("source declaration must be a small regular file")
    # O_NOFOLLOW also rejects a symlink substituted after lstat.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("source declaration is not regular")
        raw = stream.read(4097)
    if not 0 < len(raw) <= 4096:
        raise ValueError("source declaration size invalid")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    keys = {"schema", "environment", "build_id", "infra_revision"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("source declaration fields invalid")
    if (value["schema"] != "homecluster-pxe-source-declaration-v1"
            or value["environment"] != "staging"
            or not isinstance(value["build_id"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value["build_id"]) is None
            or not isinstance(value["infra_revision"], str)
            or re.fullmatch(r"[0-9a-f]{40}", value["infra_revision"]) is None):
        raise ValueError("source declaration identity invalid")
    return True


def prepare_world(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("world must be a regular file")
    old = path.read_bytes()
    lines = [line for line in old.decode("utf-8").splitlines()
             if line != "app-admin/ansible"]
    for atom in REQUIRED_ATOMS:
        if atom not in lines:
            lines.append(atom)
    new = ("\n".join(lines) + "\n").encode("utf-8")
    if new == old:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".world-update-",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    if any(re.fullmatch(r"[A-Za-z0-9_+@./:=~-]+", atom) is None for atom in args.exclude):
        print("gentoo-world-update: invalid exclude atom", file=sys.stderr)
        return 1
    try:
        if is_pinned(SOURCE_DECLARATION):
            print("gentoo-world-update: pinned PXE rootfs; OS updates use generation builds")
            return 0
        prepare_world(WORLD_FILE)
        exclude = [f"--exclude={atom}" for atom in args.exclude]
        phases = (
            ["--sync"],
            ["--noreplace", *REQUIRED_ATOMS],
            [*exclude, "-uDN", "--with-bdeps=y", "--ask=n", "--verbose",
             "--backtrack=100", "--keep-going", "@world"],
            [*exclude, "@preserved-rebuild"],
            [*exclude, "--depclean", "--ask=n"],
        )
        for phase in phases:
            subprocess.run(["/usr/bin/emerge", *phase], check=True)
    except subprocess.CalledProcessError as error:
        print("gentoo-world-update: emerge phase failed; later phases stopped", file=sys.stderr)
        return error.returncode if 0 < error.returncode < 126 else 1
    except (OSError, ValueError):
        print("gentoo-world-update: invalid declaration/world or unavailable command", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
