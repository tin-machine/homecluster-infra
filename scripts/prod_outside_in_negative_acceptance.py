#!/usr/bin/env python3
"""Render fixed, disposable O1 negative-probe acceptance resources."""

from __future__ import annotations

import json

from prod_outside_in_render import NAMESPACE, PROMETHEUS_IMAGE


NAME = "prod-o1-negative-acceptance"
BLACKBOX = "prod-blackbox-exporter"
TEST_TARGET = "https://127.0.0.1:1/readyz"
POD_LABELS = {
    "app.kubernetes.io/name": NAME,
    "app.kubernetes.io/part-of": "prod-outside-in-acceptance",
}
BLACKBOX_LABELS = {
    "app.kubernetes.io/name": BLACKBOX,
    "app.kubernetes.io/part-of": "prod-outside-in",
}


def manifest() -> dict:
    config = {
        "global": {"scrape_interval": "15s"},
        "scrape_configs": [{
            "job_name": "negative-acceptance",
            "metrics_path": "/probe",
            "params": {"module": ["http_readyz"]},
            "scrape_timeout": "10s",
            "static_configs": [{
                "targets": [TEST_TARGET],
                "labels": {"probe_name": NAME},
            }],
            "relabel_configs": [
                {"source_labels": ["__address__"], "target_label": "__param_target"},
                {"source_labels": ["__param_target"], "target_label": "instance"},
                {"target_label": "__address__", "replacement": f"{BLACKBOX}:9115"},
            ],
        }],
    }
    return {
        "apiVersion": "v1", "kind": "List", "items": [
            {
                "apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": NAME, "namespace": NAMESPACE},
                "data": {"prometheus.json": json.dumps(config, sort_keys=True)},
            },
            {
                "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": {"name": NAME, "namespace": NAMESPACE},
                "spec": {
                    "podSelector": {"matchLabels": BLACKBOX_LABELS},
                    "policyTypes": ["Ingress"],
                    "ingress": [{
                        "from": [{"podSelector": {"matchLabels": POD_LABELS}}],
                        "ports": [{"protocol": "TCP", "port": 9115}],
                    }],
                },
            },
            {
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": NAME, "namespace": NAMESPACE, "labels": POD_LABELS},
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "terminationGracePeriodSeconds": 10,
                    "securityContext": {
                        "runAsUser": 65534, "runAsGroup": 65534,
                        "runAsNonRoot": True, "fsGroup": 65534,
                    },
                    "containers": [{
                        "name": "prometheus", "image": PROMETHEUS_IMAGE,
                        "imagePullPolicy": "IfNotPresent",
                        "args": [
                            "--config.file=/etc/prometheus/prometheus.json",
                            "--storage.tsdb.path=/prometheus",
                            "--storage.tsdb.retention.time=1h",
                            "--storage.tsdb.retention.size=128MB",
                            "--web.listen-address=:9090",
                        ],
                        "ports": [{"name": "http", "containerPort": 9090}],
                        "resources": {
                            "requests": {"cpu": "25m", "memory": "64Mi"},
                            "limits": {"cpu": "150m", "memory": "256Mi"},
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "readinessProbe": {
                            "httpGet": {"path": "/-/ready", "port": "http"},
                            "periodSeconds": 5,
                        },
                        "volumeMounts": [
                            {"name": "config", "mountPath": "/etc/prometheus", "readOnly": True},
                            {"name": "data", "mountPath": "/prometheus"},
                        ],
                    }],
                    "volumes": [
                        {"name": "config", "configMap": {"name": NAME}},
                        {"name": "data", "emptyDir": {"sizeLimit": "256Mi"}},
                    ],
                },
            },
        ],
    }


if __name__ == "__main__":
    print(json.dumps(manifest(), indent=2, sort_keys=True))
