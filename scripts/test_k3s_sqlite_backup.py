#!/usr/bin/env python3
"""Fixture-only tests: no production k3s, token, or storage access."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import k3s_sqlite_backup

from k3s_sqlite_backup import backup


RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
TOKEN = b"fixture-server-token-not-a-secret\n"


class SqliteBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.dest = self.root / "external"
        (self.data / "server/db").mkdir(parents=True)
        self.dest.mkdir()
        (self.data / "server/token").write_bytes(TOKEN)
        self.db = self.data / "server/db/state.db"
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.execute("CREATE TABLE demo(value TEXT)")
        self.connection.execute("INSERT INTO demo VALUES ('uncheckpointed')")
        self.connection.commit()
        # Test the tar stream with an executable stand-in for age.
        # No real encryption is claimed or performed in fixtures.
        self.fake_age = self.root / "fake-age"
        self.fake_age.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
        self.fake_age.chmod(0o700)

        # Simulate mounted ext4 iSCSI datastore + external NFS export.
        # Real loopback/bind mounts are intentionally not used in CI.
        self.mount_records = [
            (Path("/"), "overlay", "overlay"),
            (self.data, "ext4", "/dev/fixture-iscsi"),
            (self.dest, "nfs4", "backup.example.invalid:/archive"),
        ]
        mount_patch = patch.object(k3s_sqlite_backup, "read_mountinfo", lambda: self.mount_records)
        mount_patch.start()
        self.addCleanup(mount_patch.stop)

    def backup_options(self, **kwargs):
        options = {
            "source_mount": self.data,
            "source_mount_source": "/dev/fixture-iscsi",
            "destination_mount": self.dest,
            "destination_fstype": "nfs4",
            "destination_source": "backup.example.invalid:/archive",
        }
        options.update(kwargs)
        return options


    def run_backup(self) -> Path:
        return backup(
            self.data,
            self.dest,
            RECIPIENT,
            str(self.fake_age),
            **self.backup_options(require_distinct_device=False),
        )

    def test_wal_snapshot_token_and_manifest_are_consistent(self) -> None:
        result = self.run_backup()
        self.assertEqual(result.parent, self.dest)
        self.assertTrue(result.name.endswith(".tar.age"))
        self.assertEqual(list(self.dest.iterdir()), [result])
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        with tarfile.open(result, mode="r") as archive:
            self.assertEqual(sorted(archive.getnames()), ["manifest.json", "state.db", "token"])
            state_bytes = archive.extractfile("state.db").read()
            self.assertEqual(archive.extractfile("token").read(), TOKEN)
            manifest = json.load(archive.extractfile("manifest.json"))
            self.assertEqual(manifest["sqlite_integrity"], "ok")
            self.assertEqual(manifest["state_db_sha256"], hashlib.sha256(state_bytes).hexdigest())
            self.assertEqual(manifest["server_token_sha256"], hashlib.sha256(TOKEN).hexdigest())
        restored = self.root / "restored.db"
        restored.write_bytes(state_bytes)
        with sqlite3.connect(restored) as conn:
            self.assertEqual(conn.execute("SELECT value FROM demo").fetchall(), [("uncheckpointed",)])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(sorted(self.data.iterdir()), [self.data / "server"])

    def test_missing_token_does_not_publish_partial_archive(self) -> None:
        (self.data / "server/token").unlink()
        with self.assertRaisesRegex(RuntimeError, "regular files"):
            self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))

    def test_token_rotation_during_snapshot_aborts(self) -> None:
        from unittest.mock import patch
        import k3s_sqlite_backup

        original = k3s_sqlite_backup.snapshot_sqlite

        def rotate(db: Path, output: Path) -> None:
            original(db, output)
            (self.data / "server/token").write_bytes(b"rotated-fixture-value")

        with patch.object(k3s_sqlite_backup, "snapshot_sqlite", rotate):
            with self.assertRaisesRegex(RuntimeError, "changed during snapshot"):
                self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))

    def test_reject_same_filesystem_by_default(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "different filesystem"):
            backup(self.data, self.dest, RECIPIENT, str(self.fake_age), **self.backup_options())

    def test_failed_encryption_does_not_publish_partial_archive(self) -> None:
        self.fake_age.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        with self.assertRaises((RuntimeError, BrokenPipeError)):
            self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))


    def test_alias_cannot_bypass_missing_source_mount(self) -> None:
        # /path/data/../data resolves to the same directory. Without its
        # expected ext4 mount it must fail rather than read a stale overlay DB.
        self.mount_records = [(Path("/"), "overlay", "overlay"), self.mount_records[2]]
        alias = self.data / ".." / "data"
        with self.assertRaisesRegex(RuntimeError, "expected persistent mount"):
            backup(alias, self.dest, RECIPIENT, str(self.fake_age),
                   **self.backup_options(require_distinct_device=False))
        self.assertFalse(list(self.dest.iterdir()))

    def test_alias_resolves_to_valid_mount_when_present(self) -> None:
        alias = self.data / ".." / "data"
        result = backup(alias, self.dest, RECIPIENT, str(self.fake_age),
                        **self.backup_options(require_distinct_device=False))
        self.assertTrue(result.is_file())

    def test_unmounted_destination_is_rejected(self) -> None:
        self.mount_records = self.mount_records[:2]
        with self.assertRaisesRegex(RuntimeError, "expected mounted filesystem"):
            self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))

    def test_overlay_or_tmpfs_destination_is_rejected(self) -> None:
        for fstype in ("overlay", "tmpfs"):
            with self.subTest(fstype=fstype):
                self.mount_records[2] = (self.dest, fstype, fstype)
                with self.assertRaisesRegex(RuntimeError, "expected mounted filesystem"):
                    self.run_backup()
                self.assertFalse(list(self.dest.iterdir()))

    def test_wrong_mounted_source_is_rejected(self) -> None:
        self.mount_records[2] = (self.dest, "nfs4", "other.example.invalid:/wrong")
        with self.assertRaisesRegex(RuntimeError, "expected mounted filesystem"):
            self.run_backup()

    def test_nested_tmpfs_under_expected_destination_is_rejected(self) -> None:
        child = self.dest / "subdir"
        child.mkdir()
        self.mount_records.append((child, "tmpfs", "tmpfs"))
        with self.assertRaisesRegex(RuntimeError, "expected mounted filesystem"):
            backup(self.data, child, RECIPIENT, str(self.fake_age),
                   **self.backup_options(require_distinct_device=False))

    def test_explicit_root_destination_mount_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "root filesystem"):
            backup(self.data, self.dest, RECIPIENT, str(self.fake_age),
                   **self.backup_options(destination_mount=Path("/"),
                                         require_distinct_device=False))

    def test_data_dir_unrelated_to_expected_mount_is_rejected(self) -> None:
        other = self.root / "other"
        other.mkdir()
        with self.assertRaisesRegex(RuntimeError, "expected source mountpoint"):
            backup(other, self.dest, RECIPIENT, str(self.fake_age),
                   **self.backup_options(require_distinct_device=False))

    def test_wrong_source_block_device_is_rejected(self) -> None:
        self.mount_records[1] = (self.data, "ext4", "/dev/unexpected-lun")
        with self.assertRaisesRegex(RuntimeError, "expected persistent mount"):
            self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))

    def test_source_mount_fstype_is_verified(self) -> None:
        self.mount_records[1] = (self.data, "tmpfs", "tmpfs")
        with self.assertRaisesRegex(RuntimeError, "expected persistent mount"):
            self.run_backup()

    def test_mount_removed_before_publish_is_rejected(self) -> None:
        original_snapshot = k3s_sqlite_backup.snapshot_sqlite

        def unmount_after_snapshot(db: Path, output: Path) -> None:
            original_snapshot(db, output)
            self.mount_records = self.mount_records[:2]

        with patch.object(k3s_sqlite_backup, "snapshot_sqlite", unmount_after_snapshot):
            with self.assertRaisesRegex(RuntimeError, "expected mounted filesystem"):
                self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))


    def test_reject_symlinked_destination(self) -> None:
        link = self.root / "destination-link"
        link.symlink_to(self.dest, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            backup(self.data, link, RECIPIENT, str(self.fake_age), **self.backup_options(require_distinct_device=False))


if __name__ == "__main__":
    unittest.main()
