#!/usr/bin/env python3
"""Fixture-only tests: no production k3s, token, or storage access."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

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

    def run_backup(self) -> Path:
        return backup(
            self.data,
            self.dest,
            RECIPIENT,
            str(self.fake_age),
            require_distinct_device=False,
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
            backup(self.data, self.dest, RECIPIENT, str(self.fake_age))

    def test_failed_encryption_does_not_publish_partial_archive(self) -> None:
        self.fake_age.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        with self.assertRaises((RuntimeError, BrokenPipeError)):
            self.run_backup()
        self.assertFalse(list(self.dest.iterdir()))

    def test_reject_symlinked_destination(self) -> None:
        link = self.root / "destination-link"
        link.symlink_to(self.dest, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            backup(self.data, link, RECIPIENT, str(self.fake_age), require_distinct_device=False)


if __name__ == "__main__":
    unittest.main()
