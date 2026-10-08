# Single-server k3s SQLite: encrypted online backup (manual Phase A)

The script at scripts/k3s_sqlite_backup.py is an **opt-in backup producer**,
not an automatic production deployment.

## Scope

- One k3s server using SQLite state.db.
- Online SQLite Connection.backup() includes committed WAL transactions
  without racing a sequential copy of the database and WAL/SHM.
- Integrity is checked with SQLite PRAGMA integrity_check.
- The stable server/token is included for recovery and checked again after the snapshot.
- Output: one age-encrypted tar with state.db, token, and manifest.json
  (SHA-256 checksums, UTC timestamp, integrity result).
- Only ciphertext is published to the destination via private temporary file
  and atomic rename. Transient plaintext DB snapshot lives in an owner-private
  directory on the source data-dir filesystem and is removed after completion.
- An already-existing destination on a **different filesystem** is required.
- No k3s restart, iSCSI operation, restore, timer installation or pruning.

## Manual prerequisite checks

1. Confirm the data-dir is the expected persistent mount and k3s is healthy.
   Never silently read the disposable PXE root overlay.
2. Python 3 with SQLite and the age binary are installed.
3. An external filesystem is mounted, writable by root, with adequate space.
4. Generate a dedicated age recovery identity on **another trusted host**.
   Install **only the public age recipient** on the k3s server.
5. Ensure the source filesystem has enough free space for one SQLite snapshot.
6. Check that the current k3s server really uses SQLite; embedded etcd requires
   the separate etcd-snapshot backup workflow.

Example (the recipient is a placeholder, replace before running):

    python3 scripts/k3s_sqlite_backup.py \
      --data-dir /var/lib/rancher/k3s \
      --destination /mnt/external-k3s-backups \
      --recipient 'age1REPLACE_WITH_REAL_PUBLIC_RECIPIENT'

The command reads live SQLite without stopping k3s, but creates private
temporary files under the data-dir and an encrypted archive at the destination.
Do not store backups or decryption identities in source control.

## Validation and promotion gates

Fixture-only unit tests:

    python3 -m unittest discover -s scripts -p 'test_k3s_sqlite_backup.py' -v

Before scheduling production backups or automated retention:

1. Obtain a real encrypted archive and check file size and mode.
2. Replicate the archive to a second storage failure domain.
3. On an isolated recovery host, decrypt and verify DB/token checksums, schema,
   and SQLite integrity_check.
4. Restore into a disposable, isolated k3s server and dedicated filesystem/LUN
   with a matching k3s version. NEVER attach the live production iSCSI LUN
   or let the recovery server join the production cluster.
5. Check /readyz, Kubernetes objects, node identity, and absence of traffic
   impact on prod/staging.
6. Only then add systemd timer, destination mount ordering, retention,
   telemetry/alerts and periodic restore drills.

This covers Kubernetes control-plane state only: Prometheus/Loki/PVC data
requires separate backup policies.

References:
- https://docs.k3s.io/datastore/backup-restore
- https://docs.k3s.io/datastore
- https://www.sqlite.org/backup.html
- https://www.sqlite.org/pragma.html#pragma_integrity_check
- https://age-encryption.org/
