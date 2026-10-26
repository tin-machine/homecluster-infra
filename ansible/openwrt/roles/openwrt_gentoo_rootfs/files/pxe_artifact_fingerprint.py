"""Read-only POSIX tree measurement for a quiescent build artifact.

Two descriptor-anchored streaming passes compare content and stability evidence.
The result describes the measured interval; it is not an immutable-storage seal.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

MOUNTINFO = Path("/proc/self/mountinfo")
DOMAIN = b"homecluster-pxe-artifact-tree-v1\x00"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
CHUNK = 1024 * 1024


def _unescape(value):
    escapes = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
    if re.search(r"\\(?!040|011|012|134)", value):
        raise ValueError("invalid mountinfo escape")
    return re.sub(r"\\(040|011|012|134)", lambda match: escapes[match[1]], value)


def parse_mountinfo(raw: str) -> tuple[str, ...]:
    if type(raw) is not str or not raw or "\x00" in raw:
        raise ValueError("mountinfo missing or invalid")
    result = []
    for line in raw.splitlines():
        parts = line.split(" ")
        try:
            separator = parts.index("-", 6)
        except ValueError as error:
            raise ValueError("mountinfo separator missing") from error
        if (separator < 6 or len(parts) != separator + 4 or any(not part for part in parts)
                or re.fullmatch(r"[1-9][0-9]*", parts[0]) is None
                or re.fullmatch(r"[0-9]+", parts[1]) is None
                or re.fullmatch(r"[0-9]+:[0-9]+", parts[2]) is None):
            raise ValueError("invalid mountinfo fields")
        mount_root, mountpoint = _unescape(parts[3]), _unescape(parts[4])
        if not mount_root.startswith("/") or not mountpoint.startswith("/"):
            raise ValueError("mountinfo paths must be absolute")
        if any(character in line for character in ("\t", "\r")):
            raise ValueError("unescaped mountinfo whitespace")
        result.append(mountpoint)
    if not result:
        raise ValueError("mountinfo missing")
    return tuple(result)


def _mounts(root):
    with MOUNTINFO.open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("mountinfo too large")
    points = parse_mountinfo(raw.decode("utf-8", errors="surrogateescape"))
    prefix = os.fspath(root).rstrip("/") + "/"
    if any(point != os.fspath(root) and point.startswith(prefix) for point in points):
        raise ValueError("descendant mount present")
    return raw


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, info.st_rdev)


def _same(before, after):
    if _identity(before) != _identity(after):
        raise ValueError("artifact changed during measurement")


def _attributes(target):
    options = {} if isinstance(target, int) else {"follow_symlinks": False}
    names = sorted(os.listxattr(target, **options), key=os.fsencode)
    if len(names) > 64:
        raise ValueError("xattr count exceeds budget")
    result, size = [], 0
    for name in names:
        value = os.getxattr(target, name, **options)
        size += len(value)
        if size > 262144:
            raise ValueError("xattr size exceeds budget")
        result.append([os.fsencode(name).hex(), value.hex()])
    return result


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"


def _walk(parent, name, max_entries, max_bytes):
    digest, stability = hashlib.sha256(DOMAIN), hashlib.sha256()
    links = {}
    entries, regular_bytes = 0, 0

    def visit(directory, child, relative, depth):
        nonlocal entries, regular_bytes
        entries += 1
        if entries > max_entries or depth > 128:
            raise ValueError("tree traversal budget exceeded")
        before = os.stat(child, dir_fd=directory, follow_symlinks=False)
        if not relative and not stat.S_ISDIR(before.st_mode):
            raise ValueError("root must be a directory")
        record = {"path_hex": relative.hex(), "mode": stat.S_IMODE(before.st_mode),
                  "uid": before.st_uid, "gid": before.st_gid, "mtime_ns": before.st_mtime_ns}
        descriptor = None
        try:
            if stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode):
                flags = DIRECTORY_FLAGS if stat.S_ISDIR(before.st_mode) else (
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                descriptor = os.open(child, flags, dir_fd=directory)
                _same(before, os.fstat(descriptor))
                record["xattrs"] = _attributes(descriptor)
            else:
                # The proc fd pins the parent even if its old pathname is replaced.
                anchored = os.fsencode(f"/proc/self/fd/{directory}/") + os.fsencode(child)
                record["xattrs"] = _attributes(anchored)

            if stat.S_ISDIR(before.st_mode):
                record["type"] = "directory"
            elif stat.S_ISREG(before.st_mode):
                if before.st_size < 0 or regular_bytes + before.st_size > max_bytes:
                    raise ValueError("regular bytes exceed budget")
                content, actual = hashlib.sha256(), 0
                while chunk := os.read(descriptor, CHUNK):
                    actual += len(chunk)
                    if actual > before.st_size or regular_bytes + actual > max_bytes:
                        raise ValueError("file grew or byte budget exceeded")
                    content.update(chunk)
                if actual != before.st_size:
                    raise ValueError("file size changed during read")
                regular_bytes += actual
                key = (before.st_dev, before.st_ino)
                record.update(type="regular", size=actual, content_sha256=content.hexdigest(),
                              hardlink_to=links.get(key))
                if before.st_nlink > 1:
                    links.setdefault(key, relative.hex())
            elif stat.S_ISLNK(before.st_mode):
                record.update(type="symlink", target_hex=os.fsencode(
                    os.readlink(child, dir_fd=directory)).hex())
            elif stat.S_ISFIFO(before.st_mode):
                record["type"] = "fifo"
            elif stat.S_ISCHR(before.st_mode) or stat.S_ISBLK(before.st_mode):
                record.update(type="character" if stat.S_ISCHR(before.st_mode) else "block",
                              major=os.major(before.st_rdev), minor=os.minor(before.st_rdev))
            else:
                raise ValueError("unsupported artifact file type")

            digest.update(_encoded(record))
            stability.update(_encoded([relative.hex(), _identity(before)]))
            if record["type"] == "directory":
                names = sorted(os.listdir(descriptor), key=os.fsencode)
                if len(names) > max_entries - entries:
                    raise ValueError("directory entries exceed budget")
                for name in names:
                    path = (relative + b"/" if relative else b"") + os.fsencode(name)
                    visit(descriptor, name, path, depth + 1)
                if names != sorted(os.listdir(descriptor), key=os.fsencode):
                    raise ValueError("directory entries changed")
            if descriptor is not None:
                _same(before, os.fstat(descriptor))
            _same(before, os.stat(child, dir_fd=directory, follow_symlinks=False))
        finally:
            if descriptor is not None:
                os.close(descriptor)

    visit(parent, name, b"", 0)
    return digest.hexdigest(), stability.digest(), entries, regular_bytes


def fingerprint_tree(root: Path, *, max_entries=1000000, max_bytes=1099511627776,
                     timing: dict | None = None) -> dict:
    """Measure a stable, mount-free tree; any missing evidence is an error."""
    if any(type(value) is not int or value <= 0 for value in (max_entries, max_bytes)):
        raise ValueError("budgets must be positive integers")
    descriptors, bindings = [], []
    try:
        root = Path(root)
        if ".." in root.parts:
            raise ValueError("parent traversal is not supported")
        root = root.absolute()
        mounts = _mounts(root)
        parent = os.open("/", DIRECTORY_FLAGS)
        descriptors.append(parent)
        # Hold and recheck each parent; resolving a symlink is never allowed.
        for name in root.parts[1:-1]:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            descriptors.append(descriptor)
            _same(before, os.fstat(descriptor))
            bindings.append((parent, name, descriptor, before))
            parent = descriptor
        name = root.name or "."
        first_start_ns = time.monotonic_ns()
        first = _walk(parent, name, max_entries, max_bytes)
        second_start_ns = time.monotonic_ns()
        second = _walk(parent, name, max_entries, max_bytes)
        second_finish_ns = time.monotonic_ns()
        if first != second:
            raise ValueError("artifact changed between verification passes")
        for directory, child, descriptor, before in bindings:
            # Ancestors may legitimately gain unrelated siblings during hashing.
            # Their inode/type binding must remain fixed, not their timestamps.
            for after in (os.fstat(descriptor), os.stat(child, dir_fd=directory, follow_symlinks=False)):
                if (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) != (
                        after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)):
                    raise ValueError("root ancestor binding changed")
        if mounts != _mounts(root):
            raise ValueError("mount topology changed during measurement")
        if timing is not None:
            timing.update(first_pass_ms=(second_start_ns - first_start_ns) // 1000000,
                          second_pass_ms=(second_finish_ns - second_start_ns) // 1000000,
                          entries=first[2], regular_file_bytes=first[3])
        return {"schema": "homecluster-pxe-artifact-tree-v1", "algorithm": "sha256-posix-tree-v1",
                "digest": first[0], "entries": first[2], "regular_file_bytes": first[3]}
    except (OSError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("artifact measurement unavailable") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
