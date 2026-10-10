#!/usr/bin/env python3
"""Render only the additive, source-only production Grafana O2 objects.

This renderer never reads a kubeconfig, private site input, or credential.
It does not include the existing 10 O1/O2 production resources and cannot
apply anything to a cluster.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

NAMESPACE = "observability-prod"
IMAGE = "grafana/grafana:13.2.3"
ADMIN_SECRET = "prod-grafana-admin"
CONFIG_NAME = "prod-grafana-provisioning"
DATASOURCE_UID = "prod-prometheus"
DASHBOARD_UID = "staging-outside-in"
GRAFANA = "prod-grafana"
PROMETHEUS = "prod-prometheus"


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "prod-outside-in"}


def _resource(api: str, kind: str, name: str, **values: object) -> dict:
    return {"apiVersion": api, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE}, **values}


def _panel(number: int, title: str, query: str, unit: str, x: int, y: int,
           *, legend: str = "{{node_name}}", panel_type: str = "timeseries") -> dict:
    options = (
        {"reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
         "orientation": "auto", "colorMode": "value"}
        if panel_type == "stat" else
        {"legend": {"displayMode": "table", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
    )
    return {
        "id": number, "title": title, "type": panel_type,
        "datasource": {"type": "prometheus", "uid": DATASOURCE_UID},
        "targets": [{"refId": "A", "expr": query, "legendFormat": legend}],
        "gridPos": {"x": x, "y": y, "w": 12, "h": 8},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": options,
    }


def _dashboard() -> dict:
    node = '{job="staging-node-exporter"}'
    panels = [
        _panel(1, "Staging node-exporter scrape UP (4 nodes)",
               "up" + node, "short", 0, 0, panel_type="stat"),
        _panel(2, "CPU busy (5m, %)",
               '100 * (1 - avg by (node_name) (rate(node_cpu_seconds_total{job="staging-node-exporter",mode="idle"}[5m])))',
               "percent", 12, 0),
        _panel(3, "Memory usage (%)",
               '100 * (1 - node_memory_MemAvailable_bytes' + node +
               " / node_memory_MemTotal_bytes" + node + ")", "percent", 0, 8),
        _panel(4, "Root filesystem available (%)",
               '100 * node_filesystem_avail_bytes{job="staging-node-exporter",mountpoint="/"}' +
               ' / node_filesystem_size_bytes{job="staging-node-exporter",mountpoint="/"}',
               "percent", 12, 8),
        _panel(5, "Node uptime",
               "time() - node_boot_time_seconds" + node, "s", 0, 16),
        _panel(6, "Staging API probe success",
               'probe_success{job="staging-api"}', "short", 12, 16,
               legend="{{probe_name}}", panel_type="stat"),
        _panel(7, "Staging TCP probe success",
               'probe_success{job="staging-tcp"}', "short", 0, 24,
               legend="{{probe_name}}", panel_type="stat"),
        _panel(8, "Direct scrape sample count",
               'scrape_samples_post_metric_relabeling' + node, "short", 12, 24),
    ]
    return {
        "uid": DASHBOARD_UID, "title": "Staging outside-in (prod monitoring)",
        "tags": ["outside-in", "staging", "homelab"],
        "schemaVersion": 39, "version": 1, "editable": False,
        "timezone": "browser", "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
    }


def build_manifest() -> dict:
    datasource = {
        "apiVersion": 1,
        "datasources": [{
            "name": "prod-prometheus", "uid": DATASOURCE_UID, "type": "prometheus",
            "access": "proxy", "url": "http://prod-prometheus:9090",
            "isDefault": True, "editable": False,
        }],
    }
    provider = {
        "apiVersion": 1,
        "providers": [{
            "name": "staging-outside-in", "orgId": 1, "type": "file",
            "folder": "Staging", "disableDeletion": True, "allowUiUpdates": False,
            "updateIntervalSeconds": 30,
            "options": {"path": "/etc/grafana/dashboards"},
        }],
    }
    config = {
        "datasource.yaml": _json(datasource),
        "dashboard-provider.yaml": _json(provider),
        "staging-overview.json": _json(_dashboard()),
    }
    checksum = hashlib.sha256(_json(config).encode("utf-8")).hexdigest()
    container = {
        "name": "grafana", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
        "ports": [{"name": "http", "containerPort": 3000, "protocol": "TCP"}],
        "securityContext": {
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "env": [
            {"name": "GF_AUTH_ANONYMOUS_ENABLED", "value": "false"},
            {"name": "GF_USERS_ALLOW_SIGN_UP", "value": "false"},
            {"name": "GF_ANALYTICS_REPORTING_ENABLED", "value": "false"},
            {"name": "GF_ANALYTICS_CHECK_FOR_UPDATES", "value": "false"},
            {"name": "GF_LOG_MODE", "value": "console"},
            {"name": "GF_SECURITY_ADMIN_PASSWORD",
             "valueFrom": {"secretKeyRef": {"name": ADMIN_SECRET, "key": "admin-password"}}},
            {"name": "GF_SECURITY_SECRET_KEY",
             "valueFrom": {"secretKeyRef": {"name": ADMIN_SECRET, "key": "secret-key"}}},
        ],
        "volumeMounts": [
            {"name": "datasources", "mountPath": "/etc/grafana/provisioning/datasources", "readOnly": True},
            {"name": "providers", "mountPath": "/etc/grafana/provisioning/dashboards", "readOnly": True},
            {"name": "dashboards", "mountPath": "/etc/grafana/dashboards", "readOnly": True},
            {"name": "data", "mountPath": "/var/lib/grafana"},
            {"name": "temp", "mountPath": "/tmp"},
        ],
        "readinessProbe": {
            "httpGet": {"path": "/api/health", "port": "http"}, "periodSeconds": 10,
            "initialDelaySeconds": 10,
        },
        "livenessProbe": {
            "httpGet": {"path": "/api/health", "port": "http"}, "periodSeconds": 20,
            "initialDelaySeconds": 30,
        },
        "resources": {
            "requests": {"cpu": "50m", "memory": "96Mi"},
            "limits": {"cpu": "350m", "memory": "512Mi"},
        },
    }
    volumes = [
        {"name": "datasources", "configMap": {
            "name": CONFIG_NAME, "items": [{"key": "datasource.yaml", "path": "datasource.yaml"}]}},
        {"name": "providers", "configMap": {
            "name": CONFIG_NAME, "items": [{"key": "dashboard-provider.yaml", "path": "provider.yaml"}]}},
        {"name": "dashboards", "configMap": {
            "name": CONFIG_NAME, "items": [{"key": "staging-overview.json", "path": "staging-overview.json"}]}},
        {"name": "data", "emptyDir": {"sizeLimit": "256Mi"}},
        {"name": "temp", "emptyDir": {"sizeLimit": "64Mi"}},
    ]
    return {"apiVersion": "v1", "kind": "List", "items": [
        _resource("v1", "ConfigMap", CONFIG_NAME, data=config),
        _resource("apps/v1", "Deployment", GRAFANA, spec={
            "replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": _labels(GRAFANA)},
            "template": {
                "metadata": {"labels": _labels(GRAFANA),
                             "annotations": {"checksum/provisioning": checksum}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsUser": 472, "runAsGroup": 472,
                                        "runAsNonRoot": True, "fsGroup": 472},
                    "containers": [container], "volumes": volumes,
                    "terminationGracePeriodSeconds": 30,
                },
            },
        }),
        _resource("v1", "Service", GRAFANA, spec={
            "type": "ClusterIP", "selector": _labels(GRAFANA),
            "ports": [{"name": "http", "port": 3000, "targetPort": "http",
                       "protocol": "TCP"}],
        }),
        _resource("networking.k8s.io/v1", "NetworkPolicy", "prod-grafana-restricted", spec={
            "podSelector": {"matchLabels": _labels(GRAFANA)},
            "policyTypes": ["Ingress", "Egress"], "ingress": [],
            "egress": [
                {"ports": [{"protocol": "UDP", "port": 53},
                           {"protocol": "TCP", "port": 53}]},
                {"to": [{"podSelector": {"matchLabels": _labels(PROMETHEUS)}}],
                 "ports": [{"protocol": "TCP", "port": 9090}]},
            ],
        }),
        # Additive ingress policy, not a mutation of prod-prometheus-restricted.
        _resource("networking.k8s.io/v1", "NetworkPolicy",
                  "prod-prometheus-grafana-ingress", spec={
            "podSelector": {"matchLabels": _labels(PROMETHEUS)},
            "policyTypes": ["Ingress"],
            "ingress": [{
                "from": [{"podSelector": {"matchLabels": _labels(GRAFANA)}}],
                "ports": [{"protocol": "TCP", "port": 9090}],
            }],
        }),
    ]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", help="file path; output contains no site input")
    args = p.parse_args()
    result = _json(build_manifest()) + "\n"
    if args.output:
        from pathlib import Path
        target = Path(args.output)
        if target.exists() or target.is_symlink():
            p.error("refusing to overwrite output")
        with target.open("x", encoding="utf-8") as stream:
            stream.write(result)
    else:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
