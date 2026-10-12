#!/usr/bin/env python3
"""Render additive prod Alertmanager resources for a private Slack Bot channel.

The Bot token comes from a separately provisioned Kubernetes Secret. The channel
ID is a private site input; both input and output must remain owner-only. This
module never contacts Slack or Kubernetes and does not update Prometheus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

NAMESPACE = "observability-prod"
ALERTMANAGER = "prod-alertmanager"
CONFIG = "prod-alertmanager-config"
SECRET = "prod-alertmanager-slack-bot"
SECRET_KEY = "token"
IMAGE = "quay.io/prometheus/alertmanager:v0.34.1"
CHANNEL_RE = re.compile(r"^[CG][A-Z0-9]{8,20}$")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name,
            "app.kubernetes.io/part-of": "prod-outside-in"}


def _item(api_version: str, kind: str, name: str, **extra: object) -> dict:
    return {"apiVersion": api_version, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE}, **extra}


def _private_channel(value: object) -> str:
    if not isinstance(value, str) or not CHANNEL_RE.fullmatch(value):
        raise ValueError("private Slack channel ID must be a C/G-prefixed ID")
    return value


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate private site key")
        result[key] = value
    return result


def alertmanager_config(channel_id: str) -> dict:
    channel = _private_channel(channel_id)
    return {
        "global": {"resolve_timeout": "5m"},
        "route": {
            "receiver": "slack-bot",
            "group_by": ["alertname"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "4h",
        },
        "receivers": [{
            "name": "slack-bot",
            "slack_configs": [{
                "api_url": "https://slack.com/api/chat.postMessage",
                "channel": channel,
                "send_resolved": True,
                "http_config": {"authorization": {
                    "type": "Bearer",
                    "credentials_file": "/etc/alertmanager-secret/token",
                }},
                # Do not interpolate alerts' annotations, instance/target labels,
                # generatorURL, or private log bodies into an external message.
                "title": "{{ .Status }}: {{ .CommonLabels.alertname }}",
                "text": "severity={{ .CommonLabels.severity }} status={{ .Status }}",
                "fallback": "{{ .Status }}: {{ .CommonLabels.alertname }}",
                "title_link": "",
                "pretext": "",
                "footer": "",
                "fields": [],
                "actions": [],
                "link_names": False,
            }],
        }],
    }


def build_manifest(channel_id: str) -> dict:
    config = _json(alertmanager_config(channel_id)) + "\n"
    checksum = hashlib.sha256(config.encode()).hexdigest()
    labels = _labels(ALERTMANAGER)
    pod = {
        "metadata": {"labels": labels,
                     "annotations": {"checksum/config": checksum}},
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True,
                                "runAsUser": 65534, "runAsGroup": 65534,
                                "fsGroup": 65534,
                                "fsGroupChangePolicy": "OnRootMismatch",
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "alertmanager",
                "image": IMAGE,
                "imagePullPolicy": "IfNotPresent",
                "args": ["--config.file=/etc/alertmanager/alertmanager.json",
                         "--storage.path=/alertmanager",
                         "--web.listen-address=:9093",
                         "--cluster.listen-address="],
                "ports": [{"name": "http", "containerPort": 9093,
                           "protocol": "TCP"}],
                "resources": {
                    "requests": {"cpu": "25m", "memory": "64Mi"},
                    "limits": {"memory": "256Mi"},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "readinessProbe": {"httpGet": {"path": "/-/ready", "port": "http"},
                                   "periodSeconds": 10},
                "livenessProbe": {"httpGet": {"path": "/-/healthy", "port": "http"},
                                  "periodSeconds": 20},
                "volumeMounts": [
                    {"name": "config", "mountPath": "/etc/alertmanager", "readOnly": True},
                    {"name": "slack-token", "mountPath": "/etc/alertmanager-secret",
                     "readOnly": True},
                    {"name": "data", "mountPath": "/alertmanager"},
                ],
            }],
            "volumes": [
                {"name": "config", "configMap": {"name": CONFIG,
                    "items": [{"key": "alertmanager.json", "path": "alertmanager.json"}]}},
                {"name": "slack-token", "secret": {"secretName": SECRET,
                    "items": [{"key": SECRET_KEY, "path": SECRET_KEY}]}},
                {"name": "data", "emptyDir": {"sizeLimit": "64Mi"}},
            ],
        },
    }
    objects = [
        _item("v1", "ConfigMap", CONFIG, data={"alertmanager.json": config}),
        _item("apps/v1", "Deployment", ALERTMANAGER, spec={
            "replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": labels}, "template": pod,
        }),
        _item("v1", "Service", ALERTMANAGER, spec={
            "type": "ClusterIP", "selector": labels,
            "ports": [{"name": "http", "port": 9093,
                       "targetPort": "http", "protocol": "TCP"}],
        }),
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-alertmanager-restricted", spec={
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{
                "from": [{"podSelector": {"matchLabels": _labels("prod-prometheus")}}],
                "ports": [{"protocol": "TCP", "port": 9093}],
            }],
            # Standard NetworkPolicy cannot restrict a DNS name. Keep HTTPS
            # egress scoped to this Pod; no other outbound TCP ports are open.
            "egress": [
                {"ports": [{"protocol": "UDP", "port": 53},
                           {"protocol": "TCP", "port": 53}]},
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
                 "ports": [{"protocol": "TCP", "port": 443}]},
            ],
        }),
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-prometheus-alertmanager-egress", spec={
            "podSelector": {"matchLabels": _labels("prod-prometheus")},
            "policyTypes": ["Egress"],
            "egress": [{
                "to": [{"podSelector": {"matchLabels": labels}}],
                "ports": [{"protocol": "TCP", "port": 9093}],
            }],
        }),
    ]
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def _read_site(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= 256):
            raise ValueError("site file owner, mode, or size invalid")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(257)
        if len(raw) != info.st_size:
            raise ValueError("site file size changed")
    finally:
        os.close(fd)
    site = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(site) is not dict or set(site) != {"slack_channel_id"}:
        raise ValueError("site requires exactly slack_channel_id")
    return _private_channel(site["slack_channel_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    channel_id = _read_site(args.site)
    data = (_json(build_manifest(channel_id)) + "\n").encode()
    parent = args.output.parent.stat(follow_symlinks=False)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("output directory owner or mode invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(args.output, flags, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
