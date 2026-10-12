#!/usr/bin/env python3
"""Render only the O4 authenticated push gateway; never read credential values."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat

NAMESPACE = "observability-prod"
GATEWAY = "prod-loki-push-gateway"
CONFIG = "prod-loki-push-gateway-config"
CREDENTIALS = "prod-loki-push-gateway-credentials"
IMAGE = "nginx:1.30.5-alpine3.24"
NODE_PORT = 32443
_PRIVATE_RANGES = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate site key")
        result[key] = value
    return result


def read_site(path: Path) -> tuple[str, ...]:
    """Read only an owner-controlled site file; never print its content."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= 4096):
            raise ValueError("site file owner or mode invalid")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(4097)
        if len(raw) != info.st_size:
            raise ValueError("site file size changed")
        site = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (type(site) is not dict or set(site) != {"schema", "staging_source_cidrs"}
                or type(site["schema"]) is not int or site["schema"] != 1
                or type(site["staging_source_cidrs"]) is not list
                or not 1 <= len(site["staging_source_cidrs"]) <= 4):
            raise ValueError("site schema invalid")
        cidrs = []
        for value in site["staging_source_cidrs"]:
            if type(value) is not str:
                raise ValueError("source CIDR invalid")
            try:
                network = ipaddress.ip_network(value, strict=True)
            except ValueError:
                raise ValueError("source CIDR invalid") from None
            if (network.version != 4 or network.prefixlen < 24
                    or str(network) != value
                    or not any(network.subnet_of(parent)
                               for parent in _PRIVATE_RANGES)):
                raise ValueError("source CIDR outside private allow-list")
            cidrs.append(value)
        if len(set(cidrs)) != len(cidrs):
            raise ValueError("duplicate source CIDR")
        return tuple(sorted(cidrs))
    finally:
        os.close(fd)


def _item(api: str, kind: str, name: str, **extra: object) -> dict:
    return {"apiVersion": api, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE}, **extra}


def _labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name,
            "app.kubernetes.io/part-of": "prod-outside-in"}


def nginx_config() -> str:
    """Expose only OTLP log POST; strip client auth before proxying to Loki."""
    return """worker_processes 1;
pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
    access_log off;
    error_log /dev/stderr warn;
    client_body_temp_path /tmp/client_body;
    proxy_temp_path /tmp/proxy;
    server {
        listen 8443 ssl;
        server_name _;
        ssl_certificate /run/secrets/o4/tls.crt;
        ssl_certificate_key /run/secrets/o4/tls.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        client_max_body_size 2m;
        client_body_timeout 10s;
        location = /healthz { return 200; }
        location = /otlp/v1/logs {
            limit_except POST { deny all; }
            auth_basic "staging-log-push";
            auth_basic_user_file /run/secrets/o4/htpasswd;
            proxy_set_header Authorization "";
            proxy_set_header X-Scope-OrgID "";
            proxy_set_header Host prod-loki.observability-prod.svc.cluster.local;
            proxy_connect_timeout 3s;
            proxy_send_timeout 10s;
            proxy_read_timeout 10s;
            proxy_pass http://prod-loki.observability-prod.svc.cluster.local:3100;
        }
        location / { return 404; }
    }
}
"""


def build_manifest(cidrs: tuple[str, ...]) -> dict:
    if (type(cidrs) is not tuple or not 1 <= len(cidrs) <= 4
            or len(set(cidrs)) != len(cidrs)):
        raise ValueError("source CIDRs invalid")
    for value in cidrs:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError:
            raise ValueError("source CIDR invalid") from None
        if (network.version != 4 or network.prefixlen < 24
                or str(network) != value
                or not any(network.subnet_of(parent)
                           for parent in _PRIVATE_RANGES)):
            raise ValueError("source CIDR outside private allow-list")
    config = nginx_config()
    labels = _labels(GATEWAY)
    loki_labels = _labels("prod-loki")
    objects = [
        _item("v1", "ConfigMap", CONFIG, data={"nginx.conf": config}),
        _item("apps/v1", "Deployment", GATEWAY, spec={
            "replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels, "annotations": {
                    "checksum/config": hashlib.sha256(config.encode()).hexdigest()}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True,
                                        "runAsUser": 101, "runAsGroup": 101,
                                        "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": "push-gateway", "image": IMAGE,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["nginx", "-g", "daemon off;", "-c",
                                    "/etc/nginx/nginx.conf"],
                        "ports": [{"name": "https", "containerPort": 8443,
                                   "protocol": "TCP"}],
                        "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                      "limits": {"memory": "128Mi"}},
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "readinessProbe": {"tcpSocket": {"port": "https"},
                                           "periodSeconds": 10},
                        "livenessProbe": {"tcpSocket": {"port": "https"},
                                          "periodSeconds": 20},
                        "volumeMounts": [
                            {"name": "config", "mountPath": "/etc/nginx/nginx.conf",
                             "subPath": "nginx.conf", "readOnly": True},
                            {"name": "credentials", "mountPath": "/run/secrets/o4",
                             "readOnly": True},
                            {"name": "tmp", "mountPath": "/tmp"},
                        ],
                    }],
                    "volumes": [
                        {"name": "config", "configMap": {"name": CONFIG}},
                        {"name": "credentials", "secret": {"secretName": CREDENTIALS}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}},
                    ],
                },
            },
        }),
        _item("v1", "Service", GATEWAY, spec={
            "type": "NodePort", "externalTrafficPolicy": "Local",
            "selector": labels,
            "ports": [{"name": "https", "port": 8443,
                       "targetPort": "https", "nodePort": NODE_PORT,
                       "protocol": "TCP"}],
        }),
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-loki-push-gateway-restricted", spec={
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [{"ipBlock": {"cidr": value}}
                                  for value in sorted(cidrs)],
                         "ports": [{"protocol": "TCP", "port": 8443}]}],
            "egress": [
                {"to": [{"podSelector": {"matchLabels": loki_labels}}],
                 "ports": [{"protocol": "TCP", "port": 3100}]},
                {"to": [{"namespaceSelector": {"matchLabels": {
                    "kubernetes.io/metadata.name": "kube-system"}},
                         "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                 "ports": [{"protocol": "UDP", "port": 53},
                           {"protocol": "TCP", "port": 53}]},
            ],
        }),
        _item("networking.k8s.io/v1", "NetworkPolicy",
              "prod-loki-push-gateway-to-loki", spec={
            "podSelector": {"matchLabels": loki_labels},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": labels}}],
                         "ports": [{"protocol": "TCP", "port": 3100}]}],
        }),
    ]
    return {"apiVersion": "v1", "kind": "List", "items": objects}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True,
                        help="owner-only JSON with staging_source_cidrs")
    parser.add_argument("--output", type=Path, required=True,
                        help="exclusive-create owner-only output file")
    args = parser.parse_args()
    data = _json(build_manifest(read_site(args.site))) + "\n"
    parent = args.output.parent
    parent_info = parent.stat(follow_symlinks=False)
    if (not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != os.geteuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700):
        raise ValueError("output directory owner or mode invalid")
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as dest:
        dest.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
