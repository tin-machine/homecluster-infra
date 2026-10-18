#!/usr/bin/env python3
"""Acceptance fixtures for build-time POSIX tree measurement; temporary data only."""
from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import stat
import tempfile
import types
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] / (
    "ansible/openwrt/roles/openwrt_gentoo_rootfs/files/pxe_artifact_fingerprint.py"
)
SPEC = importlib.util.spec_from_file_location("pxe_artifact_fingerprint_fixture", SOURCE)
assert SPEC and SPEC.loader
fingerprint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fingerprint)
DOMAIN = b"homecluster-pxe-artifact-tree-v1\x00"
FIXED_NS = 1700000000123456789


def oracle(root: Path) -> dict:
    """Independent small-tree oracle, deliberately unsuitable for production races."""
    records = []
    links = {}
    logical_bytes = 0

    def visit(path: Path, relative: bytes):
        nonlocal logical_bytes
        info = path.lstat()
        attrs = [
            [os.fsencode(name).hex(), os.getxattr(path, name, follow_symlinks=False).hex()]
            for name in sorted(os.listxattr(path, follow_symlinks=False), key=os.fsencode)
        ]
        entry = {
            "path_hex": relative.hex(), "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid, "mtime_ns": info.st_mtime_ns,
            "xattrs": attrs,
        }
        if stat.S_ISDIR(info.st_mode):
            entry["type"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            key = (info.st_dev, info.st_ino)
            entry.update({
                "type": "regular", "size": info.st_size,
                "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "hardlink_to": links.get(key),
            })
            links.setdefault(key, relative.hex())
            logical_bytes += info.st_size
        elif stat.S_ISLNK(info.st_mode):
            entry.update(type="symlink", target_hex=os.fsencode(os.readlink(path)).hex())
        elif stat.S_ISFIFO(info.st_mode):
            entry["type"] = "fifo"
        elif stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode):
            entry.update(type="character" if stat.S_ISCHR(info.st_mode) else "block",
                         major=os.major(info.st_rdev), minor=os.minor(info.st_rdev))
        else:
            raise AssertionError("oracle fixture has unsupported special file")
        records.append(entry)
        if entry["type"] == "directory":
            for name in sorted(os.listdir(path), key=os.fsencode):
                child = (relative + b"/" if relative else b"") + os.fsencode(name)
                visit(path / name, child)

    visit(root, b"")
    digest = hashlib.sha256(DOMAIN)
    for entry in records:
        digest.update(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n")
    return {
        "schema": "homecluster-pxe-artifact-tree-v1", "algorithm": "sha256-posix-tree-v1",
        "digest": digest.hexdigest(), "entries": len(records), "regular_file_bytes": logical_bytes,
    }


def stable_times(root: Path):
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            os.utime(Path(parent) / name, ns=(FIXED_NS, FIXED_NS), follow_symlinks=False)
        os.utime(parent, ns=(FIXED_NS, FIXED_NS), follow_symlinks=False)


class ArtifactFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pxe-artifact-fingerprint-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        previous = signal.signal(signal.SIGALRM, self.io_timeout)
        signal.alarm(10)
        self.addCleanup(self.clear_timeout, previous)

    @staticmethod
    def io_timeout(_signum, _frame):
        raise TimeoutError("fixture encountered blocking IO")

    @staticmethod
    def clear_timeout(previous):
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

    def measure(self, **options):
        return fingerprint.fingerprint_tree(self.root, **options)

    def populated(self, root=None, reverse=False):
        root = root or self.root
        (root / "etc").mkdir()
        names = ["a-", "etc/value", "z"]
        for name in reversed(names) if reverse else names:
            (root / name).write_bytes(name.encode() + b"\x00fixture\n")
        (root / "link").symlink_to("etc/value")
        os.link(root / "z", root / "hardlink")
        os.mkfifo(root / "pipe")
        stable_times(root)

    def test_exact_digest_includes_posix_metadata_and_link_topology(self):
        self.populated()
        self.assertEqual(self.measure(), oracle(self.root))

    def test_creation_order_and_absolute_root_do_not_change_identity(self):
        self.populated()
        other = self.base / "other"
        other.mkdir()
        self.populated(other, reverse=True)
        self.assertEqual(self.measure(), fingerprint.fingerprint_tree(other))

    def test_empty_directory_is_measured_without_special_success_value(self):
        self.assertEqual(self.measure(), oracle(self.root))
        self.assertEqual(self.measure()["entries"], 1)

    def test_bytes_modes_timestamps_and_symlink_targets_change_digest(self):
        self.populated()
        for change in (
            lambda: (self.root / "z").write_bytes(b"different"),
            lambda: (self.root / "z").chmod(0o700),
            lambda: os.utime(self.root / "z", ns=(FIXED_NS, FIXED_NS + 1)),
            lambda: (self.root / "link").unlink(),
            lambda: (self.root / "link").symlink_to("missing-target"),
        ):
            before = self.measure()["digest"]
            change()
            self.assertNotEqual(before, self.measure()["digest"])
            self.assertEqual(self.measure(), oracle(self.root))

    def test_same_bytes_with_different_hardlink_topology_change_digest(self):
        self.populated()
        before = self.measure()["digest"]
        data = (self.root / "hardlink").read_bytes()
        (self.root / "hardlink").unlink()
        (self.root / "hardlink").write_bytes(data)
        stable_times(self.root)
        self.assertNotEqual(before, self.measure()["digest"])

    def test_external_symlink_is_not_followed_or_read(self):
        outside = self.base / "external"
        outside.write_bytes(b"outside-one")
        (self.root / "link").symlink_to(outside)
        before = self.measure()
        outside.write_bytes(b"outside-two")
        self.assertEqual(before, self.measure())
        self.assertEqual(before["regular_file_bytes"], 0)

    def test_non_utf8_names_have_unambiguous_identity(self):
        name = os.fsdecode(b"bad-\xff\nname")
        (self.root / name).write_bytes(b"fixture")
        self.assertEqual(self.measure(), oracle(self.root))

    def test_xattr_content_changes_digest_and_is_not_disclosed(self):
        target = self.root / "file"
        target.write_bytes(b"fixture")
        os.setxattr(target, "user.fixture", b"fixture-attribute-one")
        before = self.measure()
        os.setxattr(target, "user.fixture", b"fixture-attribute-two")
        after = self.measure()
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(after, oracle(self.root))
        self.assertNotIn("fixture-attribute", json.dumps(after))

    def test_unreadable_xattrs_fail_instead_of_becoming_empty(self):
        with mock.patch.object(fingerprint.os, "listxattr", side_effect=OSError(errno.EACCES, "fixture")):
            with self.assertRaises(ValueError):
                self.measure()

    def test_root_symlink_missing_root_and_regular_root_are_rejected(self):
        link = self.base / "link"
        link.symlink_to(self.root)
        file = self.base / "file"
        file.write_bytes(b"fixture")
        parent_link = self.base / "parent-link"
        parent_link.symlink_to(self.base)
        for path in (link, self.base / "absent", file, parent_link / "root"):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                fingerprint.fingerprint_tree(path)

    def test_socket_is_rejected_and_fifo_is_not_opened(self):
        os.mkfifo(self.root / "pipe")
        self.assertEqual(self.measure(), oracle(self.root))
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(self.root / "socket"))
            with self.assertRaises(ValueError):
                self.measure()

    def test_device_metadata_is_hashed_without_opening_devices(self):
        target = self.root / "device"
        target.write_bytes(b"must-not-be-read-as-a-device")
        actual_stat = os.stat
        actual_open = os.open
        for kind in (stat.S_IFCHR, stat.S_IFBLK):
            def device_stat(path, *args, **kwargs):
                info = actual_stat(path, *args, **kwargs)
                if not isinstance(path, int) and os.fsdecode(path).split("/")[-1] == "device":
                    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                    values.update(st_mode=kind | 0o600, st_rdev=os.makedev(1, 3))
                    return types.SimpleNamespace(**values)
                return info

            def no_device_open(path, *args, **kwargs):
                if not isinstance(path, int) and os.fsdecode(path).split("/")[-1] == "device":
                    self.fail("fingerprinter tried to open a device")
                return actual_open(path, *args, **kwargs)

            with self.subTest(kind=kind), mock.patch.object(fingerprint.os, "stat", side_effect=device_stat), mock.patch.object(fingerprint.os, "open", side_effect=no_device_open):
                self.assertEqual(self.measure(), oracle(self.root))

    def test_budget_exhaustion_and_bad_budgets_fail_closed(self):
        (self.root / "file").write_bytes(b"12345")
        for kwargs in ({"max_entries": 1}, {"max_bytes": 4}, {"max_entries": True}, {"max_bytes": -1}, {"max_entries": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.measure(**kwargs)

    def test_mutation_during_read_is_rejected_even_with_restored_mtime(self):
        target = self.root / "file"
        target.write_bytes(b"before")
        original = target.stat()
        actual_read = os.read
        changed = False

        def changing_read(fd, amount):
            nonlocal changed
            if not changed and os.fstat(fd).st_ino == original.st_ino:
                changed = True
                target.write_bytes(b"after!")
                os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
            return actual_read(fd, amount)

        with mock.patch.object(fingerprint.os, "read", side_effect=changing_read):
            with self.assertRaises(ValueError):
                self.measure()
        self.assertTrue(changed, "fixture did not exercise the descriptor read")

    def test_replaced_root_during_read_is_not_published_as_original_path(self):
        target = self.root / "file"
        target.write_bytes(b"before")
        original_inode = target.stat().st_ino
        actual_read = os.read
        changed = False

        def replacing_read(fd, amount):
            nonlocal changed
            if not changed and os.fstat(fd).st_ino == original_inode:
                changed = True
                self.root.rename(self.base / "old-root")
                self.root.mkdir()
            return actual_read(fd, amount)

        with mock.patch.object(fingerprint.os, "read", side_effect=replacing_read):
            with self.assertRaises(ValueError):
                self.measure()
        self.assertTrue(changed)

    def test_mountinfo_escapes_and_nested_mount_rejection(self):
        nested = self.root / "space dir"
        nested.mkdir()
        escaped = str(nested).replace(" ", "\\040")
        raw = f"1 0 8:1 / / rw - ext4 /dev/fixture rw\n2 1 8:1 / {escaped} rw - ext4 /dev/fixture rw\n"
        self.assertEqual(fingerprint.parse_mountinfo(raw), ("/", str(nested)))
        mountinfo = self.base / "mountinfo"
        mountinfo.write_text(raw)
        with mock.patch.object(fingerprint, "MOUNTINFO", mountinfo):
            with self.assertRaises(ValueError):
                self.measure()

    def test_mountinfo_unavailable_or_malformed_is_not_no_mounts(self):
        for raw in ("", "garbage\n", "1 0 8:1 / relative rw - ext4 /dev/fixture rw\n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                fingerprint.parse_mountinfo(raw)
        with mock.patch.object(fingerprint, "MOUNTINFO", self.base / "absent"):
            with self.assertRaises(ValueError):
                self.measure()

    def test_mount_of_root_itself_is_allowed_but_not_nested_mounts(self):
        mountinfo = self.base / "mountinfo"
        mountinfo.write_text(f"1 0 8:1 / {self.root} rw - ext4 /dev/fixture rw\n")
        with mock.patch.object(fingerprint, "MOUNTINFO", mountinfo):
            self.assertEqual(self.measure(), oracle(self.root))


if __name__ == "__main__":
    unittest.main()
