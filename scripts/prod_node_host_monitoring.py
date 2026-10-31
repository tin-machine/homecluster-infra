"""Opt-in production host exporter and Grafana view; no live reads or writes."""
from __future__ import annotations

import hashlib
import json
import re

NAMESPACE = "observability-prod"
EXPORTER = "prod-node-exporter"
IMAGE = "quay.io/prometheus/node-exporter:v1.9.1"


def exporter(node_name: str) -> dict:
    if not isinstance(node_name, str) or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", node_name) is None:
        raise ValueError("production exporter requires an explicit node hostname")
    labels = {"app.kubernetes.io/name": EXPORTER,
              "app.kubernetes.io/part-of": "prod-outside-in"}
    collectors = ("cpu", "cpufreq", "meminfo", "filesystem", "loadavg", "netdev",
                  "stat", "uname", "pressure", "diskstats", "hwmon", "thermal_zone")
    return {
        "apiVersion": "apps/v1", "kind": "DaemonSet",
        "metadata": {"name": EXPORTER, "namespace": NAMESPACE, "labels": labels},
        "spec": {
            "selector": {"matchLabels": labels},
            "updateStrategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1}},
            "template": {"metadata": {"labels": labels}, "spec": {
                "automountServiceAccountToken": False,
                "nodeSelector": {"kubernetes.io/hostname": node_name, "kubernetes.io/os": "linux"},
                "hostNetwork": True, "hostPID": True, "dnsPolicy": "ClusterFirstWithHostNet",
                "tolerations": [{"key": "node-role.kubernetes.io/" + role,
                                 "operator": "Exists", "effect": "NoSchedule"}
                                for role in ("control-plane", "master")],
                "securityContext": {"runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": 65534},
                "containers": [{
                    "name": "node-exporter", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                    "env": [{"name": "HOST_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}}}],
                    "args": ["--web.listen-address=$(HOST_IP):9100", "--path.rootfs=/host",
                             "--path.procfs=/host/proc", "--path.sysfs=/host/sys",
                             "--collector.disable-defaults",
                             "--collector.filesystem.mount-points-exclude=^/(dev|proc|sys|run|var/lib/(kubelet|rancher/k3s/agent/containerd))($|/)",
                             "--collector.filesystem.fs-types-exclude=^(autofs|binfmt_misc|bpf|cgroup2?|configfs|debugfs|devpts|devtmpfs|fusectl|hugetlbfs|mqueue|nsfs|proc|procfs|pstore|rpc_pipefs|securityfs|selinuxfs|squashfs|sysfs|tracefs)$",
                             *["--collector." + collector for collector in collectors]],
                    "ports": [{"name": "metrics", "containerPort": 9100, "protocol": "TCP"}],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                        "seccompProfile": {"type": "RuntimeDefault"}},
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                  "limits": {"cpu": "200m", "memory": "96Mi"}},
                    "readinessProbe": {"httpGet": {"path": "/metrics", "port": "metrics"},
                                       "periodSeconds": 15, "timeoutSeconds": 5},
                    "volumeMounts": [{"name": "host-root", "mountPath": "/host",
                                      "readOnly": True, "mountPropagation": "HostToContainer"}],
                }],
                "volumes": [{"name": "host-root", "hostPath": {"path": "/", "type": "Directory"}}],
            }},
        },
    }


def dashboard() -> dict:
    from prod_host_monitoring_dashboard import panel
    selector = '{job="prod-node-exporter"}'
    rows = [
        ("Scrape UP", "up" + selector, "short", "{{node_name}}", "stat"),
        ("CPU busy (5m)", '100 * (1 - avg by (node_name) (rate(node_cpu_seconds_total{job="prod-node-exporter",mode="idle"}[5m])))', "percent", "{{node_name}}", "timeseries"),
        ("Available memory", "node_memory_MemAvailable_bytes" + selector, "bytes", "{{node_name}}", "timeseries"),
        ("Memory usage", "100 * (1 - node_memory_MemAvailable_bytes" + selector + " / node_memory_MemTotal_bytes" + selector + ")", "percent", "{{node_name}}", "timeseries"),
        ("Filesystem available", 'node_filesystem_avail_bytes{job="prod-node-exporter",mountpoint=~"/|/var/lib/rancher/k3s|/srv/k3s-storage"}', "bytes", "{{node_name}} {{mountpoint}}", "timeseries"),
        ("Host uptime", "time() - node_boot_time_seconds" + selector, "s", "{{node_name}}", "stat"),
        ("Load (1m)", "node_load1" + selector, "short", "{{node_name}}", "timeseries"),
        ("Temperature", "node_hwmon_temp_celsius" + selector + " or node_thermal_zone_temp" + selector, "celsius", "{{node_name}} {{chip}} {{sensor}} {{zone}}", "timeseries"),
        ("Disk read rate", "rate(node_disk_read_bytes_total" + selector + "[5m])", "Bps", "{{node_name}} {{device}}", "timeseries"),
        ("Disk write rate", "rate(node_disk_written_bytes_total" + selector + "[5m])", "Bps", "{{node_name}} {{device}}", "timeseries"),
        ("Network receive", 'rate(node_network_receive_bytes_total{job="prod-node-exporter",device!~"lo|veth.*|cali.*"}[5m])', "Bps", "{{node_name}} {{device}}", "timeseries"),
        ("Network transmit", 'rate(node_network_transmit_bytes_total{job="prod-node-exporter",device!~"lo|veth.*|cali.*"}[5m])', "Bps", "{{node_name}} {{device}}", "timeseries"),
    ]
    return {
        "uid": "prod-hosts", "title": "Production hosts", "tags": ["production", "hosts"],
        "schemaVersion": 39, "version": 1, "editable": False, "timezone": "browser",
        "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
        "panels": [panel(n + 1, title, expr, unit, legend=legend, kind=kind)
                   for n, (title, expr, unit, legend, kind) in enumerate(rows)],
    }


def add_dashboard(manifest: dict) -> None:
    config = next(item for item in manifest["items"] if item["kind"] == "ConfigMap"
                  and item["metadata"]["name"] == "prod-grafana-provisioning")["data"]
    deployment = next(item for item in manifest["items"] if item["kind"] == "Deployment"
                      and item["metadata"]["name"] == "prod-grafana")
    providers = json.loads(config["dashboard-provider.yaml"])
    providers["providers"].append({
        "name": "production-hosts", "orgId": 1, "type": "file", "folder": "Production",
        "folderUid": "prod-hosts", "disableDeletion": True, "allowUiUpdates": False,
        "updateIntervalSeconds": 30, "options": {"path": "/etc/grafana/prod-host-dashboards"},
    })
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    config["dashboard-provider.yaml"] = encode(providers)
    config["prod-hosts.json"] = encode(dashboard())
    template = deployment["spec"]["template"]
    template["metadata"]["annotations"]["checksum/provisioning"] = hashlib.sha256(
        encode(config).encode()).hexdigest()
    template["spec"]["containers"][0]["volumeMounts"].append({
        "name": "prod-host-dashboards", "mountPath": "/etc/grafana/prod-host-dashboards", "readOnly": True,
    })
    template["spec"]["volumes"].append({"name": "prod-host-dashboards", "configMap": {
        "name": "prod-grafana-provisioning", "items": [{"key": "prod-hosts.json", "path": "prod-hosts.json"}],
    }})
