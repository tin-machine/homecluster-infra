#!/usr/bin/env python3
"""Read Helm Secret labels through Kubernetes metadata-only API negotiation.

The fixed root kubeconfig is read locally. Its key remains in process memory;
neither credentials nor Secret payloads are printed or stored by this probe.
The latest revision label is not chart health or Terraform/source equivalence.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Any, Callable, Sequence
import yaml

SCHEMA = "homecluster-helm-metadata-observation-v1"
SCOPE = "helm_revision_labels_not_values_or_health_attested"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
KUBECONFIG = Path("/etc/rancher/k3s/k3s.yaml")
ACCEPT = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1"
API_PATH = "/api/v1/secrets?labelSelector=owner%3Dhelm&limit=128"
BOOT = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
STATUSES = {"deployed", "superseded", "failed", "pending-install",
            "pending-upgrade", "pending-rollback", "uninstalled", "uninstalling"}
MAX_RAW_BYTES = 256 * 1024
MAX_RECORDS = 128
MAX_RELEASES = 32


def unknown(reason: str) -> dict:
    return {"schema": SCHEMA, "status": "unknown", "reason": reason,
            "scope": SCOPE, "boot_id": None, "observed_at": None,
            "revision_count": None, "failed_revision_count": None,
            "pending_count": None, "releases": []}


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate API field")
        value[key] = item
    return value


def _decode_pem(value: Any) -> bytes:
    if not isinstance(value, str) or not 0 < len(value) <= 32768:
        raise ValueError("credential encoding invalid")
    raw = base64.b64decode(value, validate=True)
    if not 0 < len(raw) <= 16384 or not raw.startswith(b"-----BEGIN "):
        raise ValueError("credential PEM invalid")
    return raw


def _tls_context(kubeconfig_path: Path = KUBECONFIG) -> ssl.SSLContext:
    fd = os.open(kubeconfig_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_size <= 0 or info.st_size > 65536):
            raise ValueError("kubeconfig metadata invalid")
        raw = os.read(fd, 65537)
        if len(raw) != info.st_size:
            raise ValueError("kubeconfig changed during read")
    finally:
        os.close(fd)
    config = yaml.safe_load(raw)
    if not isinstance(config, dict):
        raise ValueError("kubeconfig invalid")
    selected = config.get("current-context")
    contexts = [entry for entry in config.get("contexts", [])
                if isinstance(entry, dict) and entry.get("name") == selected]
    if len(contexts) != 1 or not isinstance(contexts[0].get("context"), dict):
        raise ValueError("kubeconfig context invalid")
    binding = contexts[0]["context"]
    clusters = [entry.get("cluster") for entry in config.get("clusters", [])
                if isinstance(entry, dict) and entry.get("name") == binding.get("cluster")]
    users = [entry.get("user") for entry in config.get("users", [])
             if isinstance(entry, dict) and entry.get("name") == binding.get("user")]
    if (len(clusters) != 1 or len(users) != 1
            or not isinstance(clusters[0], dict) or not isinstance(users[0], dict)
            or clusters[0].get("server") != "https://127.0.0.1:6443"):
        raise ValueError("kubeconfig endpoint invalid")
    ca = _decode_pem(clusters[0].get("certificate-authority-data"))
    cert = _decode_pem(users[0].get("client-certificate-data"))
    key = _decode_pem(users[0].get("client-key-data"))
    context = ssl.create_default_context(cadata=ca.decode("ascii"))
    cert_fd = None
    key_fd = None
    try:
        cert_fd = os.memfd_create("homecluster-helm-client-cert", os.MFD_CLOEXEC)
        key_fd = os.memfd_create("homecluster-helm-client-key", os.MFD_CLOEXEC)
        for fd, payload in ((cert_fd, cert), (key_fd, key)):
            remaining = memoryview(payload)
            while remaining:
                remaining = remaining[os.write(fd, remaining):]
        context.load_cert_chain(f"/proc/self/fd/{cert_fd}", f"/proc/self/fd/{key_fd}")
    finally:
        if cert_fd is not None:
            os.close(cert_fd)
        if key_fd is not None:
            os.close(key_fd)
    return context


def _fetch_metadata(*, connection_factory=http.client.HTTPSConnection,
                    context_factory=_tls_context) -> dict:
    connection = connection_factory("127.0.0.1", 6443,
                                    context=context_factory(), timeout=8)
    try:
        connection.request("GET", API_PATH, headers={"Accept": ACCEPT})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("metadata API unavailable")
        raw = response.read(MAX_RAW_BYTES + 1)
        if not 0 < len(raw) <= MAX_RAW_BYTES:
            raise ValueError("metadata API response size invalid")
    finally:
        connection.close()
    value = json.loads(raw, object_pairs_hook=_unique)
    if (not isinstance(value, dict)
            or value.get("kind") != "PartialObjectMetadataList"
            or value.get("apiVersion") != "meta.k8s.io/v1"):
        raise ValueError("metadata-only response required")
    return value


def _parse(value: dict) -> tuple[list[dict], int, int, int]:
    if not isinstance(value, dict) or set(value) != {"kind", "apiVersion", "metadata", "items"}:
        raise ValueError("metadata list invalid")
    meta = value["metadata"]
    if not isinstance(meta, dict) or meta.get("continue") not in (None, ""):
        raise ValueError("metadata list truncated")
    items = value["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_RECORDS:
        raise ValueError("metadata cardinality invalid")
    seen: set[tuple[str, str, int]] = set()
    latest: dict[tuple[str, str], dict] = {}
    failed = 0
    for item in items:
        if (not isinstance(item, dict)
                or set(item) != {"apiVersion", "kind", "metadata"}
                or item["apiVersion"] != "meta.k8s.io/v1"
                or item["kind"] != "PartialObjectMetadata"
                or not isinstance(item["metadata"], dict)):
            raise ValueError("full Secret object or invalid metadata")
        metadata = item["metadata"]
        labels = metadata.get("labels")
        if not isinstance(labels, dict) or labels.get("owner") != "helm":
            raise ValueError("Helm label invalid")
        namespace, name = metadata.get("namespace"), labels.get("name")
        status, revision_text = labels.get("status"), labels.get("version")
        if (NAME.fullmatch(namespace) is None or NAME.fullmatch(name) is None
                or status not in STATUSES or not isinstance(revision_text, str)
                or not revision_text.isascii()
                or not revision_text.isdigit() or not 1 <= len(revision_text) <= 6):
            raise ValueError("metadata identity invalid")
        revision = int(revision_text)
        if (revision < 1 or str(revision) != revision_text
                or (namespace, name, revision) in seen):
            raise ValueError("metadata revision invalid")
        seen.add((namespace, name, revision))
        if status == "failed":
            failed += 1
        key = (namespace, name)
        if key not in latest or latest[key]["revision"] < revision:
            latest[key] = {"namespace": namespace, "name": name,
                           "revision": revision, "status": status}
    if len(latest) > MAX_RELEASES:
        raise ValueError("release cardinality invalid")
    releases = sorted(latest.values(), key=lambda item: (item["namespace"], item["name"]))
    pending = sum(item["status"].startswith("pending-") for item in releases)
    return releases, failed, pending, len(items)


def snapshot(*, boot_path: Path = BOOT_ID,
             fetcher: Callable[[], dict] = _fetch_metadata,
             now: datetime | None = None) -> dict:
    """Fixture boot/fetcher may be injected; CLI exposes no host or command."""
    try:
        first = boot_path.read_text(encoding="ascii").strip()
        if BOOT.fullmatch(first) is None:
            return unknown("boot_identity_invalid")
        releases, failed, pending, revision_count = _parse(fetcher())
        second = boot_path.read_text(encoding="ascii").strip()
        if second != first:
            return unknown("boot_identity_changed")
        observed = datetime.now(timezone.utc) if now is None else now
        if observed.utcoffset() is None:
            return unknown("observation_time_invalid")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError,
            OverflowError, binascii.Error, yaml.YAMLError, ssl.SSLError):
        return unknown("helm_metadata_unavailable_or_invalid")
    return {"schema": SCHEMA, "status": "partial",
            "reason": "helm_metadata_observed_no_values_attestation", "scope": SCOPE,
            "boot_id": first,
            "observed_at": observed.astimezone(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "revision_count": revision_count,
            "failed_revision_count": failed, "pending_count": pending,
            "releases": releases}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed staging Helm metadata reader")
    parser.add_argument("--json", action="store_true", required=True)
    parser.parse_args(argv)
    result = snapshot()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
