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

# Recognize an actual kernel mount, not merely a path on a different st_dev.
# /proc/self/mountinfo also distinguishes nested mounts and bind mounts.
PERSISTENT_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs", "zfs", "nfs", "nfs4"})


def _unescape_mount_field(value: str) -> str:
    import re

    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def read_mountinfo() -> list[tuple[Path, str, str]]:
    mounts: list[tuple[Path, str, str]] = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        pre, separator, post = line.partition(" - ")
        if not separator:
            raise RuntimeError("invalid mountinfo record")
        fields, fs = pre.split(), post.split()
        if len(fields) < 5 or len(fs) < 2:
            raise RuntimeError("invalid mountinfo fields")
        mounts.append(
            (Path(_unescape_mount_field(fields[4])),
             _unescape_mount_field(fs[0]),
             _unescape_mount_field(fs[1]))
        )
    if not mounts:
        raise RuntimeError("mountinfo is empty")
    return mounts


def effective_mount(path: Path, mounts: list[tuple[Path, str, str]]) -> tuple[Path, str, str]:
    # Last record wins on stacked mounts at the same path.
    matching = [
        mount for mount in mounts
        if path == mount[0] or mount[0] in path.parents
    ]
    if not matching:
        raise RuntimeError("no mountinfo entry for path")
    return max(enumerate(matching), key=lambda entry: (len(entry[1][0].parts), entry[0]))[1]


def validate_mounts(
    data_dir: Path,
    destination: Path,
    source_mount: Path,
    destination_mount: Path,
    destination_fstype: str,
    destination_source: str,
    source_fstype: str = "ext4",
    source_mount_source: str = "",
    require_distinct_device: bool = True,
) -> None:
    # Canonicalize every CLI-supplied path before comparison. Never special-case
    # the literal DEFAULT_DATA_DIR string: ../ and symlink aliases must not bypass checks.
    source_real = data_dir.resolve(strict=True)
    expected_source = source_mount.resolve(strict=True)
    destination_real = destination.resolve(strict=True)
    expected_destination = destination_mount.resolve(strict=True)

    if source_real != expected_source:
        raise RuntimeError("data-dir must equal its expected source mountpoint")
    if not (
        destination_real == expected_destination or
        expected_destination in destination_real.parents
    ):
        raise RuntimeError("destination must reside under its expected mountpoint")
    if expected_destination == Path("/"):
        raise RuntimeError("root filesystem is not a valid backup destination mount")
    if source_fstype not in PERSISTENT_FILESYSTEMS or destination_fstype not in PERSISTENT_FILESYSTEMS:
        raise RuntimeError("expected filesystem must be persistent")
    if not destination_source or not source_mount_source:
        raise RuntimeError("both expected mount sources must be specified")

    mounts = read_mountinfo()
    src = effective_mount(source_real, mounts)
    dst = effective_mount(destination_real, mounts)
    if src != (expected_source, source_fstype, source_mount_source):
        raise RuntimeError("k3s data-dir is not the expected persistent mount")
    if dst != (expected_destination, destination_fstype, destination_source):
        raise RuntimeError("backup destination is not the expected mounted filesystem")
    if require_distinct_device and source_real.stat().st_dev == destination_real.stat().st_dev:
        raise RuntimeError("destination must be on a different filesystem")




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
    source_mount: Path = DEFAULT_DATA_DIR,
    destination_mount: Path,
    destination_fstype: str,
    destination_source: str,
    source_fstype: str = "ext4",
    source_mount_source: str,
    require_distinct_device: bool = True,  # Internal fixture-only override; never exposed by CLI.
) -> Path:
    if not recipient.startswith("age1") or any(c.isspace() for c in recipient):
        raise ValueError("expected an age X25519 public recipient")
    if not data_dir.is_dir() or data_dir.is_symlink():
        raise RuntimeError("k3s data directory missing or symlinked")
    if not destination.is_dir() or destination.is_symlink():
        raise RuntimeError("destination directory must exist and must not be a symlink")
    validate_mounts(
        data_dir, destination, source_mount, destination_mount,
        destination_fstype, destination_source, source_fstype,
        source_mount_source, require_distinct_device,
    )

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
            # Refuse a lost/replaced mount before writing to an overlay fallback.
            validate_mounts(
                data_dir, destination, source_mount, destination_mount,
                destination_fstype, destination_source, source_fstype,
                source_mount_source, require_distinct_device,
            )
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
            # Never publish if the mount changed during encryption.
            validate_mounts(
                data_dir, destination, source_mount, destination_mount,
                destination_fstype, destination_source, source_fstype,
                source_mount_source, require_distinct_device,
            )
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
    parser.add_argument("--source-mount-source", required=True, help="expected mountinfo source of k3s data-dir, e.g. iSCSI block partition")
    parser.add_argument("--destination", type=Path, required=True, help="existing external directory")
    parser.add_argument("--destination-mount", type=Path, required=True, help="expected mounted backup filesystem root")
    parser.add_argument("--destination-fstype", required=True, help="expected persistent filesystem type, e.g. nfs4/ext4")
    parser.add_argument("--destination-source", required=True, help="expected mountinfo source, e.g. NFS export or block device")
    parser.add_argument("--recipient", required=True, help="age public recipient (not an identity)")
    args = parser.parse_args()
    try:
        output = backup(
            args.data_dir, args.destination, args.recipient,
            destination_mount=args.destination_mount,
            destination_fstype=args.destination_fstype,
            destination_source=args.destination_source,
            source_mount_source=args.source_mount_source,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        # Do not include the token, source contents, or age stderr in stdout/stderr.
        print(f"backup failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"backup created: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
