"""Opt-in QNAP TS-469/QTS 4.3.4 SNMP profile and production Grafana view."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re

NAMESPACE = "observability-prod"
EXPORTER = "prod-nas-snmp-exporter"
CONFIG = "prod-nas-snmp-config"
IMAGE = "prom/snmp-exporter:v0.31.0"
JOB = "qnap-nas-snmp"
MODULE = "qnap_legacy"
AUTH = "qnap_v3"
B = "1.3.6.1.4.1.24681.1.3"


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate(address: str, username: str) -> None:
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", username):
        raise ValueError("invalid SNMPv3 monitoring username")
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        raise ValueError("NAS requires a specific private IPv4 address") from None
    if (str(parsed) != address or not parsed.is_private or parsed.is_loopback
            or parsed.is_link_local or parsed.is_multicast or parsed.is_unspecified or parsed.is_reserved):
        raise ValueError("NAS requires a specific private IPv4 address")


def snmp_config(username: str = "prometheus") -> dict:
    # This is a calibrated legacy profile, not a claim about every QNAP firmware.
    # NAS-MIB incorrectly describes free memory and volume capacities as bytes:
    # this firmware returns KiB. Physical disk and total memory are already bytes.
    scalars = [
        ("qnap_cpu_usage_percent", 1, 1, "NAS CPU usage in percent."),
        ("qnap_memory_total_bytes", 2, 1, "NAS total memory in bytes."),
        ("qnap_memory_free_bytes", 3, 1024, "NAS-reported free memory, not Linux MemAvailable."),
        ("qnap_uptime_seconds", 4, 0.01, "Host uptime converted from TimeTicks to seconds."),
        ("qnap_cpu_temperature_celsius", 5, 1, "CPU temperature in Celsius."),
        ("qnap_system_temperature_celsius", 6, 1, "System temperature in Celsius."),
    ]
    metrics = [{"name": name, "oid": f"{B}.{number}", "type": "gauge",
                "scale": scale, "help": help_text}
               for name, number, scale, help_text in scalars]
    columns = [
        ("qnap_disk_temperature_celsius", B + ".11.1.3", "gauge", "disk", 1),
        ("qnap_disk_status", B + ".11.1.4", "gauge", "disk", 1),
        ("qnap_disk_capacity_bytes", B + ".11.1.6", "gauge", "disk", 1),
        ("qnap_disk_smart_info", B + ".11.1.7", "DisplayString", "disk", 1),
        ("qnap_fan_rpm", B + ".15.1.3", "gauge", "fan", 1),
        ("qnap_volume_size_bytes", B + ".17.1.4", "gauge", "volume", 1024),
        ("qnap_volume_free_bytes", B + ".17.1.5", "gauge", "volume", 1024),
        ("qnap_volume_status", B + ".17.1.6", "DisplayString", "volume", 1),
        ("qnap_raid_status", "1.3.6.1.4.1.24681.1.4.1.1.1.2.1.2.1.5", "DisplayString", "raid", 1),
        ("qnap_interface_info", "1.3.6.1.2.1.2.2.1.2", "DisplayString", "interface", 1),
        ("qnap_network_receive_bytes_total", "1.3.6.1.2.1.31.1.1.1.6", "counter", "interface", 1),
        ("qnap_network_transmit_bytes_total", "1.3.6.1.2.1.31.1.1.1.10", "counter", "interface", 1),
    ]
    for name, oid, kind, index, scale in columns:
        metrics.append({"name": name, "oid": oid, "type": kind, "scale": scale,
                        "help": name.replace("_", " ") + ".",
                        "indexes": [{"labelname": index, "type": "gauge"}]})
    return {
        "auths": {AUTH: {"version": 3, "username": username, "security_level": "noAuthNoPriv"}},
        "modules": {MODULE: {"get": [f"{B}.{number}.0" for _, number, _, _ in scalars],
                             "walk": [column[1] for column in columns],
                             "metrics": metrics, "timeout": "3s", "retries": 0,
                             "max_repetitions": 10}},
    }


def labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "prod-outside-in"}


def resource(kind: str, name: str, **parts: object) -> dict:
    api = {"Deployment": "apps/v1", "NetworkPolicy": "networking.k8s.io/v1"}.get(kind, "v1")
    return {"apiVersion": api, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels(name)}, **parts}


def resources(address: str, username: str) -> list[dict]:
    validate(address, username)
    text = encode(snmp_config(username))
    selector = {"matchLabels": labels(EXPORTER)}
    prom = {"matchLabels": labels("prod-prometheus")}
    return [
        resource("ConfigMap", CONFIG, data={"snmp.json": text}),
        resource("Service", EXPORTER, spec={
            "type": "ClusterIP", "selector": labels(EXPORTER),
            "ports": [{"name": "http", "port": 9116, "targetPort": "http", "protocol": "TCP"}]}),
        resource("NetworkPolicy", "prod-nas-snmp-restricted", spec={
            "podSelector": selector, "policyTypes": ["Ingress", "Egress"],
            "ingress": [{"from": [{"podSelector": prom}],
                         "ports": [{"port": 9116, "protocol": "TCP"}]}],
            "egress": [{"to": [{"ipBlock": {"cidr": address + "/32"}}],
                        "ports": [{"port": 161, "protocol": "UDP"}]}]}),
        resource("NetworkPolicy", "prod-nas-prometheus-egress", spec={
            "podSelector": prom, "policyTypes": ["Egress"],
            "egress": [{"to": [{"podSelector": selector}],
                        "ports": [{"port": 9116, "protocol": "TCP"}]}]}),
        resource("Deployment", EXPORTER, spec={
            "replicas": 1, "selector": selector, "strategy": {"type": "Recreate"},
            "template": {"metadata": {"labels": labels(EXPORTER), "annotations": {
                "checksum/config": hashlib.sha256(text.encode()).hexdigest()}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 65534, "runAsGroup": 65534},
                    "containers": [{
                        "name": "snmp-exporter", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                        "args": ["--config.file=/etc/snmp/snmp.json", "--web.listen-address=:9116"],
                        "ports": [{"name": "http", "containerPort": 9116, "protocol": "TCP"}],
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]},
                                            "seccompProfile": {"type": "RuntimeDefault"}},
                        "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                      "limits": {"cpu": "200m", "memory": "128Mi"}},
                        "readinessProbe": {"httpGet": {"path": "/", "port": "http"}, "timeoutSeconds": 3},
                        "livenessProbe": {"httpGet": {"path": "/", "port": "http"}, "timeoutSeconds": 3,
                                          "initialDelaySeconds": 10},
                        "volumeMounts": [{"name": "config", "mountPath": "/etc/snmp", "readOnly": True}],
                    }],
                    "volumes": [{"name": "config", "configMap": {"name": CONFIG}}],
                }},
        }),
    ]


def dashboard() -> dict:
    from prod_host_monitoring_dashboard import panel
    s = '{job="' + JOB + '"}'
    network_info = (' * on(instance,interface) group_left(qnap_interface_info) '
                    'qnap_interface_info{job="' + JOB + '",qnap_interface_info!="lo"}')
    rows = [
        ("Scrape UP", "up" + s, "short", "", "stat"),
        ("CPU usage", "qnap_cpu_usage_percent" + s, "percent", "", "timeseries"),
        ("NAS-reported free memory", "qnap_memory_free_bytes" + s, "bytes", "", "timeseries"),
        ("NAS-reported memory usage", "100 * (1 - qnap_memory_free_bytes" + s
         + " / qnap_memory_total_bytes" + s + ")", "percent", "", "timeseries"),
        ("Host uptime", "qnap_uptime_seconds" + s, "s", "", "stat"),
        ("CPU and system temperature", '{__name__=~"qnap_(cpu|system)_temperature_celsius",job="'
         + JOB + '"}', "celsius", "{{__name__}}", "timeseries"),
        ("Disk temperature", "qnap_disk_temperature_celsius" + s, "celsius", "Disk {{disk}}", "timeseries"),
        ("Fan speed", "qnap_fan_rpm" + s, "rpm", "Fan {{fan}}", "timeseries"),
        ("Volume capacity", "qnap_volume_size_bytes" + s, "bytes", "Volume {{volume}}", "timeseries"),
        ("Volume free", "qnap_volume_free_bytes" + s, "bytes", "Volume {{volume}}", "timeseries"),
        ("Volume used", "100 * (1 - qnap_volume_free_bytes" + s + " / qnap_volume_size_bytes"
         + s + ")", "percent", "Volume {{volume}}", "timeseries"),
        ("Disk status", "qnap_disk_status" + s, "short", "Disk {{disk}}", "stat"),
        ("Disk SMART", "qnap_disk_smart_info" + s, "short", "", "table"),
        ("RAID state", "qnap_raid_status" + s, "short", "", "table"),
        ("Volume state", "qnap_volume_status" + s, "short", "", "table"),
        ("Network receive (interfaces shown separately)",
         "rate(qnap_network_receive_bytes_total" + s + "[5m])" + network_info,
         "Bps", "{{qnap_interface_info}}", "timeseries"),
        ("Network transmit (interfaces shown separately)",
         "rate(qnap_network_transmit_bytes_total" + s + "[5m])" + network_info,
         "Bps", "{{qnap_interface_info}}", "timeseries"),
    ]
    panels = [panel(i + 1, title, expr, unit, legend=legend, kind=kind)
              for i, (title, expr, unit, legend, kind) in enumerate(rows)]
    for item in panels:
        if item["title"] == "Disk status":
            item["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
                str(code): {"text": text, "color": color}
                for code, text, color in [(0, "Ready", "green"), (-5, "No disk", "yellow"),
                                         (-6, "Invalid", "red"), (-9, "R/W error", "red"),
                                         (-4, "Unknown", "gray")]}}]
        if item["type"] == "table":
            item["targets"][0].update(format="table", instant=True)
            item["transformations"] = [{"id": "organize", "options": {"excludeByName": {
                "Time": True, "__name__": True, "job": True, "instance": True, "Value": True}}}]
    return {"uid": "qnap-nas", "title": "QNAP NAS", "tags": ["nas", "qnap"],
            "schemaVersion": 39, "version": 1, "editable": False, "timezone": "browser",
            "refresh": "1m", "time": {"from": "now-6h", "to": "now"}, "panels": panels}


def add_monitoring(manifest: dict, address: str, username: str) -> None:
    validate(address, username)
    items = manifest["items"]
    def find(kind: str, name: str) -> dict:
        return next(x for x in items if x["kind"] == kind and x["metadata"]["name"] == name)
    cm = find("ConfigMap", "prod-prometheus-config")
    cfg = json.loads(cm["data"]["prometheus.json"])
    cfg["scrape_configs"].append({
        "job_name": JOB, "metrics_path": "/snmp", "scheme": "http",
        "scrape_interval": "60s", "scrape_timeout": "30s", "sample_limit": 500,
        "params": {"auth": [AUTH], "module": [MODULE]},
        "static_configs": [{"targets": [address + ":161"]}],
        "relabel_configs": [
            {"source_labels": ["__address__"], "target_label": "__param_target"},
            {"source_labels": ["__param_target"], "target_label": "instance"},
            {"target_label": "__address__", "replacement": EXPORTER + ":9116"}],
        "metric_relabel_configs": [{"action": "keep", "source_labels": ["__name__"],
                                    "regex": "qnap_.*|snmp_scrape_.*"}],
    })
    update_prometheus_config(manifest, cfg)
    data = find("ConfigMap", "prod-grafana-provisioning")["data"]
    providers = json.loads(data["dashboard-provider.yaml"])
    providers["providers"].append({
        "name": "qnap-nas", "orgId": 1, "type": "file", "folder": "NAS", "folderUid": "qnap-nas",
        "disableDeletion": True, "allowUiUpdates": False, "updateIntervalSeconds": 30,
        "options": {"path": "/etc/grafana/nas-dashboards"}})
    data["dashboard-provider.yaml"] = encode(providers)
    data["qnap-nas.json"] = encode(dashboard())
    template = find("Deployment", "prod-grafana")["spec"]["template"]
    template["metadata"]["annotations"]["checksum/provisioning"] = hashlib.sha256(encode(data).encode()).hexdigest()
    template["spec"]["containers"][0]["volumeMounts"].append({
        "name": "nas-dashboards", "mountPath": "/etc/grafana/nas-dashboards", "readOnly": True})
    template["spec"]["volumes"].append({"name": "nas-dashboards", "configMap": {
        "name": "prod-grafana-provisioning", "items": [{"key": "qnap-nas.json", "path": "qnap-nas.json"}]}})
    items.extend(resources(address, username))


def update_prometheus_config(manifest: dict, config: dict) -> None:
    """Keep configuration and the mounted alert-rule text in the rollout digest."""
    import prod_alert_rules_render as rules
    text = json.dumps(config, sort_keys=True)
    cm = next(x for x in manifest["items"] if x["kind"] == "ConfigMap"
              and x["metadata"]["name"] == "prod-prometheus-config")
    cm["data"]["prometheus.json"] = text
    rule_text = rules.build_manifest()["items"][0]["data"][rules.RULE_KEY]
    deployment = next(x for x in manifest["items"] if x["kind"] == "Deployment"
                      and x["metadata"]["name"] == "prod-prometheus")
    deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"] = hashlib.sha256(
        (text + "\n" + rule_text).encode()).hexdigest()
