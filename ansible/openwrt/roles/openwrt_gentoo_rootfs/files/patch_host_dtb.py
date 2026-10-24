#!/usr/bin/env python3
"""Create a host-local DTB without mutating its pinned release artifact."""

import os
import pathlib
import re
import stat
import sys
import tempfile


NEEDLE = b"cgroup_disable=memory"


def patch(source: pathlib.Path, dest: pathlib.Path) -> bool:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"release DTB is not a regular file: {source}")
    if not dest.parent.is_dir():
        raise ValueError(f"host DTB directory is missing: {dest.parent}")
    if dest.is_symlink():
        # An older run may have left a link to this or an earlier dated release.
        target = pathlib.PurePosixPath(os.readlink(dest))
        if target.parts[:2] != ("..", "dates") or len(target.parts) != 4:
            raise ValueError(f"unexpected host DTB symlink: {dest}")
        if not re.fullmatch(r"\d{8}(?:-[0-9A-Za-z._-]+)?", target.parts[2]) or target.parts[3] != dest.name:
            raise ValueError(f"unexpected host DTB symlink: {dest}")
    elif dest.exists() and not dest.is_file():
        raise ValueError(f"host DTB is not a regular file: {dest}")

    data = source.read_bytes().replace(NEEDLE, b" " * len(NEEDLE))
    if not dest.is_symlink() and dest.exists() and dest.read_bytes() == data:
        return False

    fd, temp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, stat.S_IMODE(source.stat().st_mode))
        os.replace(temp_name, dest)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


if __name__ == "__main__":
    try:
        changed = patch(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print("patched" if changed else "unchanged")
