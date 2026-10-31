"""Opt-in, NAS-scoped UDP syslog receiver; internal Loki remains private."""
from __future__ import annotations

import hashlib
import json
import re

from prod_nas_monitoring import encode, labels, resource, validate

NAME = "prod-nas-syslog"
CONFIG = NAME + "-config"
METRICS = NAME + "-metrics"
IMAGE = "grafana/alloy:v1.21.3"
COLLECTOR_JOB = "qnap-nas-collector"
NODE_PORT = 31514


def alloy_config() -> str:
    # Old QNAP uses strftime("%b %d %T"): Nov 01 is rejected by the strict
    # RFC3164 parser. Parse this bounded legacy header with receipt timestamps.
    # The raw source still parses PRI and exposes severity to relabel rules.
    # Only a bounded month marker is temporary; hostname/PID are never labels.
    header = (r"^(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
              r"(?: [1-9]|0[1-9]|[12][0-9]|3[01]) "
              r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9] [^ ]+ (?P<body>.+)$")
    rules = []
    for regex, level in [("0|emerg|emergency", "emerg"), ("1|alert", "alert"),
                         ("2|crit|critical", "critical"), ("3|err|error", "error"),
                         ("4|warn|warning", "warn"), ("5|notice", "notice"),
                         ("6|info|informational", "info"), ("7|debug", "debug")]:
        rules.append('  rule {\n    source_labels = ["__syslog_message_severity"]\n'
                     f'    regex = "{regex}"\n    target_label = "level"\n'
                     f'    replacement = "{level}"\n  }}')
    return '''loki.relabel "nas" {
  forward_to = []
''' + "\n".join(rules) + '''
}
loki.source.syslog "nas" {
  listener {
    address = "0.0.0.0:1514"
    protocol = "udp"
    syslog_format = "raw"
    max_message_length = 8192
    udp_queue_size = 128
    udp_host_cache_size = 16
    labels = {
      environment = "production",
      source = "qnap-syslog",
      service_name = "qnap-nas",
      level = "unknown",
    }
  }
  relabel_rules = loki.relabel.nas.rules
  forward_to = [loki.process.nas.receiver]
}
loki.process "nas" {
  forward_to = [loki.write.prod.receiver]
  stage.regex {
    expression = ''' + json.dumps(header) + '''
  }
  stage.labels {
    values = { nas_month = "month", }
  }
  stage.match {
    selector = "{source=\\"qnap-syslog\\",nas_month=\\"\\"}"
    action = "drop"
    drop_counter_reason = "nas_invalid_header"
  }
  stage.match {
    selector = "{source=\\"qnap-syslog\\",level=\\"unknown\\"}"
    action = "drop"
    drop_counter_reason = "nas_invalid_severity"
  }
  stage.label_drop {
    values = ["nas_month"]
  }
  stage.output {
    source = "body"
  }
}
loki.write "prod" {
  max_streams = 16
  endpoint {
    name = "prod-nas"
    url = "http://prod-loki:3100/loki/api/v1/push"
    batch_size = "128KiB"
    batch_wait = "1s"
    remote_timeout = "5s"
    min_backoff_period = "500ms"
    max_backoff_period = "5s"
    max_backoff_retries = 3
  }
}
'''


def resources(address: str, node: str) -> list[dict]:
    validate(address, "prometheus")
    if not isinstance(node, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", node):
        raise ValueError("syslog requires an explicit production node hostname")
    text = alloy_config()
    selector = {"matchLabels": labels(NAME)}
    prom = {"matchLabels": labels("prod-prometheus")}
    loki = {"matchLabels": labels("prod-loki")}
    return [
        resource("ConfigMap", CONFIG, data={"config.alloy": text}),
        resource("Service", NAME, spec={
            "type": "NodePort", "externalTrafficPolicy": "Local", "selector": labels(NAME),
            "ports": [{"name": "syslog", "port": 1514, "targetPort": "syslog", "nodePort": NODE_PORT,
                       "protocol": "UDP"}]}),
        resource("Service", METRICS, spec={
            "type": "ClusterIP", "selector": labels(NAME),
            "ports": [{"name": "http", "port": 12345, "targetPort": "http", "protocol": "TCP"}]}),
        resource("NetworkPolicy", "prod-nas-syslog-restricted", spec={
            "podSelector": selector, "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {"from": [{"ipBlock": {"cidr": address + "/32"}}],
                 "ports": [{"port": 1514, "protocol": "UDP"}]},
                {"from": [{"podSelector": prom}], "ports": [{"port": 12345, "protocol": "TCP"}]}],
            "egress": [
                {"to": [{"podSelector": loki}], "ports": [{"port": 3100, "protocol": "TCP"}]},
                {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                         "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                 "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]}]}),
        resource("NetworkPolicy", "prod-nas-loki-ingress", spec={
            "podSelector": loki, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": selector}],
                         "ports": [{"port": 3100, "protocol": "TCP"}]}]}),
        resource("Deployment", NAME, spec={
            "replicas": 1, "selector": selector, "strategy": {"type": "Recreate"},
            "template": {"metadata": {"labels": labels(NAME), "annotations": {
                "checksum/config": hashlib.sha256(text.encode()).hexdigest()}}, "spec": {
                    "nodeSelector": {"kubernetes.io/hostname": node, "kubernetes.io/os": "linux"},
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 473,
                                        "runAsGroup": 473, "fsGroup": 473},
                    "tolerations": [{"key": "node-role.kubernetes.io/" + role,
                                     "operator": "Exists", "effect": "NoSchedule"}
                                    for role in ("control-plane", "master")],
                    "containers": [{
                        "name": "alloy", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                        "args": ["run", "--disable-reporting", "--stability.level=experimental",
                                 "--server.http.listen-addr=0.0.0.0:12345",
                                 "--storage.path=/var/lib/alloy", "/etc/alloy/config.alloy"],
                        "ports": [{"name": "syslog", "containerPort": 1514, "protocol": "UDP"},
                                  {"name": "http", "containerPort": 12345, "protocol": "TCP"}],
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]},
                                            "seccompProfile": {"type": "RuntimeDefault"}},
                        "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                      "limits": {"cpu": "200m", "memory": "192Mi"}},
                        "readinessProbe": {"httpGet": {"path": "/-/ready", "port": "http"}},
                        "livenessProbe": {"httpGet": {"path": "/-/healthy", "port": "http"},
                                          "initialDelaySeconds": 10},
                        "volumeMounts": [{"name": "config", "mountPath": "/etc/alloy", "readOnly": True},
                                         {"name": "data", "mountPath": "/var/lib/alloy"}],
                    }],
                    "volumes": [{"name": "config", "configMap": {"name": CONFIG}},
                                {"name": "data", "emptyDir": {"sizeLimit": "32Mi"}}],
                }},
        }),
    ]


def add_syslog(manifest: dict, address: str, node: str) -> None:
    additions = resources(address, node)
    def find(kind: str, name: str) -> dict:
        return next(x for x in manifest["items"] if x["kind"] == kind and x["metadata"]["name"] == name)
    prom = find("ConfigMap", "prod-prometheus-config")
    cfg = json.loads(prom["data"]["prometheus.json"])
    cfg["scrape_configs"].append({
        "job_name": COLLECTOR_JOB, "scrape_interval": "60s", "scrape_timeout": "5s", "sample_limit": 1000,
        "static_configs": [{"targets": [METRICS + ":12345"]}],
        "metric_relabel_configs": [{"action": "keep", "source_labels": ["__name__"],
                                    "regex": "loki_source_syslog_.*|loki_process_.*|loki_write_.*|alloy_component_.*"}]})
    from prod_nas_monitoring import update_prometheus_config
    update_prometheus_config(manifest, cfg)
    find("NetworkPolicy", "prod-nas-prometheus-egress")["spec"]["egress"].append({
        "to": [{"podSelector": {"matchLabels": labels(NAME)}}],
        "ports": [{"port": 12345, "protocol": "TCP"}]})
    data = find("ConfigMap", "prod-grafana-provisioning")["data"]
    dash = json.loads(data["qnap-nas.json"])
    from prod_host_monitoring_dashboard import panel
    n = len(dash["panels"])
    dash["panels"].append(panel(n + 1, "Syslog collector UP",
                               'up{job="' + COLLECTOR_JOB + '"}', "short", kind="stat"))
    dash["panels"].append({
        "id": n + 2, "title": "NAS system logs", "type": "logs",
        "datasource": {"type": "loki", "uid": "prod-loki"},
        "targets": [{"refId": "A", "expr": '{environment="production",source="qnap-syslog"}'}],
        "gridPos": {"x": 0, "y": ((n + 2) // 2) * 8, "w": 24, "h": 12},
        "options": {"showTime": True, "showLabels": False, "enableLogDetails": True,
                    "wrapLogMessage": True, "sortOrder": "Descending"}})
    data["qnap-nas.json"] = encode(dash)
    template = find("Deployment", "prod-grafana")["spec"]["template"]
    template["metadata"]["annotations"]["checksum/provisioning"] = hashlib.sha256(encode(data).encode()).hexdigest()
    manifest["items"].extend(additions)
