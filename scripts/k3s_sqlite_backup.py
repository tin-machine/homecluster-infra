#!/usr/bin/env python3
"""Create an encrypted, consistent online backup of a single-server k3s SQLite datastore.

No k3s restart, iSCSI action, restore, or retention/pruning is performed.
The destination contains only an age-encrypted tar; secrets are never logged.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4


DEFAULT_DATA_DIR = Path("/var/lib/rancher/k3s")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite(db: Path, output: Path) -> None:
    # URI mode=ro is WAL-aware. Do not use immutable=1, which can ignore the WAL.
    db_uri = "file:" + quote(str(db), safe="/") + "?mode=ro"
    with sqlite3.connect(db_uri, uri=True, timeout=5) as source:
        source.execute("PRAGMA busy_timeout=5000")
        with sqlite3.connect(output) as target:
            source.backup(target, pages=256, sleep=0.1)
    with sqlite3.connect(f"file:{quote(str(output), safe='/')}?mode=ro", uri=True) as check:
        result = check.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise RuntimeError("SQLite snapshot integrity_check failed")


def add_bytes(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(value))


def add_database(archive: tarfile.TarFile, snapshot: Path) -> None:
    info = tarfile.TarInfo("state.db")
    info.size = snapshot.stat().st_size
    info.mode = 0o600
    with snapshot.open("rb") as stream:
        archive.addfile(info, stream)


def backup(
    data_dir: Path,
    destination: Path,
    recipient: str,
    age_bin: str = "age",
    *,
    require_distinct_device: bool = True,
) -> Path:
    if not recipient.startswith("age1") or any(c.isspace() for c in recipient):
        raise ValueError("expected an age X25519 public recipient")
    if not data_dir.is_dir() or data_dir.is_symlink():
        raise RuntimeError("k3s data directory missing or symlinked")
    if data_dir == DEFAULT_DATA_DIR and not os.path.ismount(data_dir):
        raise RuntimeError("k3s data directory is not a mountpoint")
    if not destination.is_dir() or destination.is_symlink():
        raise RuntimeError("destination directory must exist and must not be a symlink")
    if require_distinct_device and data_dir.stat().st_dev == destination.stat().st_dev:
        raise RuntimeError("destination must be on a different filesystem")

    db = data_dir / "server/db/state.db"
    token = data_dir / "server/token"
    if not db.is_file() or not token.is_file() or db.is_symlink() or token.is_symlink():
        raise RuntimeError("state.db and server/token must be regular files")
    # Resolve age before touching the live datastore.
    executable = shutil.which(age_bin)
    if executable is None:
        raise RuntimeError("age executable unavailable")

    token_before = token.read_bytes()
    if not token_before:
        raise RuntimeError("server token is empty")

    created = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"k3s-sqlite-{created}-{uuid4().hex[:12]}.tar.age"
    partial: Path | None = None
    try:
        # This transient plaintext is protected by mode 0700 on the k3s ext4 mount.
        # Nothing plaintext is persisted to the backup destination.
        with tempfile.TemporaryDirectory(prefix=".sqlite-backup-", dir=data_dir) as staging:
            snapshot = Path(staging) / "state.db"
            snapshot_sqlite(db, snapshot)
            if token.read_bytes() != token_before:
                raise RuntimeError("server token changed during snapshot")
            metadata = {
                "schema": "k3s-sqlite-backup-v1",
                "created_utc": created,
                "database": "server/db/state.db",
                "sqlite_integrity": "ok",
                "state_db_sha256": sha256_file(snapshot),
                "server_token_sha256": hashlib.sha256(token_before).hexdigest(),
            }
            fd, raw_path = tempfile.mkstemp(prefix=".k3s-sqlite-", suffix=".partial", dir=destination)
            partial = Path(raw_path)
            with os.fdopen(fd, "wb") as encrypted:
                process = subprocess.Popen(
                    [executable, "-r", recipient],
                    stdin=subprocess.PIPE,
                    stdout=encrypted,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    assert process.stdin is not None
                    with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
                        add_database(archive, snapshot)
                        add_bytes(archive, "token", token_before)
                        add_bytes(
                            archive, "manifest.json",
                            (json.dumps(metadata, sort_keys=True) + "\n").encode(),
                        )
                    process.stdin.close()
                    if process.wait(timeout=120) != 0:
                        raise RuntimeError("age encryption failed")
                except BaseException:
                    process.kill() if process.poll() is None else None
                    process.wait()
                    raise
                encrypted.flush()
                os.fsync(encrypted.fileno())
            finished = destination / name
            os.rename(partial, finished)
            partial = None
            return finished
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--destination", type=Path, required=True, help="existing external directory")
    parser.add_argument("--recipient", required=True, help="age public recipient (not an identity)")
    args = parser.parse_args()
    try:
        output = backup(args.data_dir, args.destination, args.recipient)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        # Do not include the token, source contents, or age stderr in stdout/stderr.
        print(f"backup failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"backup created: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
