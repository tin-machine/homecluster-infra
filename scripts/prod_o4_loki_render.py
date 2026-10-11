#!/usr/bin/env python3
"""Render only the additive production O4 Loki storage backend.

No production runtime access, no remote write endpoint, and no site-local data.
The single-tenant Loki API is intentionally ClusterIP/NetworkPolicy-restricted;
a separately reviewed authenticated gateway is required before staging writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

NAMESPACE = "observability-prod"
LOKI = "prod-loki"
GRAFANA = "prod-grafana"
CONFIG = "prod-loki-config"
DATA = "prod-loki-data"
IMAGE = "grafana/loki:3.7.8"
PERSISTENCE_REQUEST = "4Gi"
RETENTION = "72h"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name,
            "app.kubernetes.io/part-of": "prod-outside-in"}


def _item(api_version: str, kind: str, name: str, **extra: object) -> dict:
    return {"apiVersion": api_version, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE}, **extra}


def loki_config() -> dict:
    """Monolithic, filesystem TSDB, with actual compactor retention enabled."""
    return {
        "auth_enabled": False,
        "analytics": {"reporting_enabled": False},
        "server": {"http_listen_port": 3100, "log_level": "info"},
        "common": {
            # Monolithic components talk over loopback, not the Pod IP.
            # This is required before applying deny-all Loki egress.
            "instance_addr": "127.0.0.1",
            "path_prefix": "/var/loki",
            "replication_factor": 1,
            "ring": {"kvstore": {"store": "inmemory"}},
            "storage": {"filesystem": {
                "chunks_directory": "/var/loki/chunks",
                "rules_directory": "/var/loki/rules",
            }},
        },
        "schema_config": {"configs": [{
            "from": "2024-01-01", "store": "tsdb", "object_store": "filesystem",
            "schema": "v13", "index": {"prefix": "prod_index_", "period": "24h"},
        }]},
        "storage_config": {"tsdb_shipper": {
            "active_index_directory": "/var/loki/index",
            "cache_location": "/var/loki/index-cache",
        }},
        "compactor": {
            "working_directory": "/var/loki/compactor",
            "compaction_interval": "10m",
            "retention_enabled": True,
            "delete_request_store": "filesystem",
        },
        "limits_config": {
            "allow_structured_metadata": True,
            "retention_period": RETENTION,
            "ingestion_rate_mb": 1,
            "ingestion_burst_size_mb": 2,
            "max_global_streams_per_user": 1000,
        },
    }


def build_manifest() -> dict:
    config = _json(loki_config()) + "\n"
    checksum = hashlib.sha256(config.encode("utf-8")).hexdigest()
    labels = _labels(LOKI)
    pod = {
        "metadata": {
            "labels": labels, "annotations": {"checksum/config": checksum},
        },
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True, "runAsUser": 10001,
                "runAsGroup": 10001, "fsGroup": 10001,
                "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "terminationGracePeriodSeconds": 60,
            "containers": [{
                "name": "loki",
                "image": IMAGE,
                "imagePullPolicy": "IfNotPresent",
                "args": ["-config.file=/etc/loki/loki.yaml"],
                "ports": [{
                    "name": "http", "containerPort": 3100, "protocol": "TCP",
                }],
                "resources": {
                    "requests": {"cpu": "50m", "memory": "256Mi"},
                    "limits": {"memory": "768Mi"},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "startupProbe": {
                    "httpGet": {"path": "/ready", "port": "http"},
                    "periodSeconds": 10, "failureThreshold": 30,
                },
                "readinessProbe": {
                    "httpGet": {"path": "/ready", "port": "http"},
                    "periodSeconds": 10,
                },
                "livenessProbe": {
                    "httpGet": {"path": "/ready", "port": "http"},
                    "periodSeconds": 20, "failureThreshold": 6,
                },
                "volumeMounts": [
                    {"name": "config", "mountPath": "/etc/loki", "readOnly": True},
                    {"name": "data", "mountPath": "/var/loki"},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }],
            "volumes": [
                {"name": "config", "configMap": {
                    "name": CONFIG, "items": [{
                        "key": "loki.yaml", "path": "loki.yaml",
                    }],
                }},
                {"name": "data", "persistentVolumeClaim": {"claimName": DATA}},
                {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
            ],
        },
    }
    objects = [
        _item("v1", "ConfigMap", CONFIG, data={"loki.yaml": config}),
        _item("v1", "PersistentVolumeClaim", DATA, spec={
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "volumeMode": "Filesystem",
            "resources": {"requests": {"storage": PERSISTENCE_REQUEST}},
        }),
        _item("apps/v1", "Deployment", LOKI, spec={
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": labels},
            "template": pod,
        }),
        _item("v1", "Service", LOKI, spec={
            "type": "ClusterIP",
            "selector": labels,
            "ports": [{
                "name": "http", "port": 3100,
                "targetPort": "http", "protocol": "TCP",
            }],
        }),
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-loki-restricted", spec={
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            # No ingress from staging. Future authenticated gateway requires
            # separately reviewed additive NetworkPolicy.
            "ingress": [{
                "from": [{"podSelector": {
                    "matchLabels": _labels(GRAFANA),
                }}],
                "ports": [{"protocol": "TCP", "port": 3100}],
            }],
            "egress": [],
        }),
        # Extend Grafana's egress without changing any existing object.
        # NetworkPolicy allows are additive across policies.
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-grafana-loki-egress", spec={
            "podSelector": {"matchLabels": _labels(GRAFANA)},
            "policyTypes": ["Egress"],
            "egress": [{
                "to": [{"podSelector": {"matchLabels": labels}}],
                "ports": [{"protocol": "TCP", "port": 3100}],
            }],
        }),
    ]
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="exclusive-create file (never overwrite)")
    args = parser.parse_args()
    data = _json(build_manifest()) + "\n"
    if args.output is None:
        sys.stdout.write(data)
        return 0
    path = Path(args.output)
    # No follow, exclusive-create, owner-only; do not touch existing output.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as dest:
        dest.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
