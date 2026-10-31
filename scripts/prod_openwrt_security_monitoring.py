"""Add isolated log monitoring; leave the existing notification policy untouched."""
from __future__ import annotations

import hashlib
import json

UID = "openwrt-security"
LOKI = "prod-loki"
HEARTBEAT = ('{service_name="openwrt-security"} | sensor="openwrt-one" '
             '| log_type="heartbeat" | security_parser_status="ok"')
ANALYSIS = '{service_name="pcap-analysis"} | security_parser_status="ok"'
MUTE = "openwrt-security-night"


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def queries() -> dict[str, str]:
    return {
        "heartbeat": "absent_over_time(" + HEARTBEAT + " [5m])",
        "feed": "sum(count_over_time(" + HEARTBEAT
                + ' | banip_feed_verification!="verified" [5m]))',
        "parser": 'sum(count_over_time({service_name=~"openwrt-security|pcap-analysis"}'
                  ' | security_parser_status="invalid" [5m]))',
        "analysis": "sum(count_over_time(" + ANALYSIS
                    + ' | event_type="analysis_summary" | analysis_status="failed" [10m]))',
        "coordinator": "sum(count_over_time(" + ANALYSIS
                       + ' | event_type="coordinator_blocked" [10m]))',
    }


def rule(key: str, expression: str, title: str, pending: str) -> dict:
    return {
        "uid": "ow-security-" + key, "title": title, "condition": "C",
        "data": [
            {"refId": "A", "queryType": "instant", "relativeTimeRange": {"from": 600, "to": 0},
             "datasourceUid": LOKI, "model": {
                 "datasource": {"type": "loki", "uid": LOKI}, "expr": expression,
                 "queryType": "instant", "instant": True, "refId": "A",
                 "intervalMs": 1000, "maxDataPoints": 43200}},
            {"refId": "B", "relativeTimeRange": {"from": 0, "to": 0},
             "datasourceUid": "__expr__", "model": {
                 "type": "reduce", "expression": "A", "reducer": "last", "refId": "B",
                 "datasource": {"type": "__expr__", "uid": "__expr__"}}},
            {"refId": "C", "relativeTimeRange": {"from": 0, "to": 0},
             "datasourceUid": "__expr__", "model": {
                 "type": "threshold", "expression": "B", "refId": "C",
                 "datasource": {"type": "__expr__", "uid": "__expr__"},
                 "conditions": [{"type": "query", "evaluator": {"type": "gt", "params": [0]},
                                 "operator": {"type": "and"}, "query": {"params": ["C"]},
                                 "reducer": {"type": "last", "params": []}}]}},
        ],
        # An empty vector is normal for absence/error-count expressions. Query
        # failures must still alert; neither missing values nor errors become 0.
        "noDataState": "OK", "execErrState": "Alerting", "for": pending,
        "labels": {"scope": UID, "severity": "warning"},
        "annotations": {"summary": title, "__dashboardUid__": UID,
                        "__panelId__": str({"heartbeat": 1, "feed": 1, "parser": 3,
                                           "analysis": 4, "coordinator": 6}[key])}, "isPaused": False,
        "notification_settings": {
            "receiver": UID, "group_by": ["grafana_folder", "alertname"],
            "group_wait": "30s", "group_interval": "5m", "repeat_interval": "4h",
            "mute_time_intervals": [MUTE],
        },
    }


def provisioning(*, paused: bool = False) -> dict:
    if not isinstance(paused, bool):
        raise ValueError("invalid security alert pause flag")
    titles = {
        "heartbeat": "OpenWrt security heartbeat欠落",
        "feed": "OpenWrt reputation feed検証異常",
        "parser": "Security log parser異常",
        "analysis": "Offline解析失敗（侵害判定ではありません）",
        "coordinator": "Offline解析または再配送の受付停止",
    }
    result = {
        "apiVersion": 1,
        "groups": [{"orgId": 1, "name": UID, "folder": "OpenWrt security", "interval": "60s",
                    "rules": [rule(k, v, titles[k], "5m" if k in ("heartbeat", "feed", "parser") else "0s")
                              for k, v in queries().items()]}],
        # Reuse the existing authenticated Slack delivery in Alertmanager. No
        # token/channel copy, internet egress, root policy, or Prometheus changes.
        "contactPoints": [{"orgId": 1, "name": UID, "receivers": [{
            "uid": "ow-security-alertmanager", "type": "prometheus-alertmanager",
            "disableResolveMessage": False,
            "settings": {"url": "http://prod-alertmanager:9093"},
        }]}],
        "muteTimes": [{"orgId": 1, "name": MUTE, "time_intervals": [{
            "times": [{"start_time": "01:00", "end_time": "06:30"}],
            "location": "Asia/Tokyo",
        }]}],
    }
    for item in result["groups"][0]["rules"]:
        item["isPaused"] = paused
    return result


def dashboard() -> dict:
    specifications = [
        ("Heartbeat・feed別検証（取得失敗/不明はfreshではありません）", HEARTBEAT, "logs"),
        ("最終feed検証からの秒数（IPv4 2 feeds、欠測は不明）",
         "max_over_time(" + HEARTBEAT
         + ' | unwrap banip_feed_max_age_seconds | __error__="" [5m])', "timeseries"),
        ("監視parser異常", '{service_name=~"openwrt-security|pcap-analysis"}'
         ' | security_parser_status="invalid"', "logs"),
        ("解析完了・失敗・engine/rules版", ANALYSIS + ' | event_type="analysis_summary"', "logs"),
        ("Suricata検知候補（検証SIDを含む・侵害確定ではありません）",
         ANALYSIS + ' | event_type="detection_candidate"', "logs"),
        ("解析・配送の受付停止", ANALYSIS + ' | event_type="coordinator_blocked"', "logs"),
        ("banIP受信ログ（rate limit後の件数、全drop数ではありません）",
         '{service_name="openwrt-security"} | log_type="kernel"', "logs"),
    ]
    panels = []
    for i, (title, expression, kind) in enumerate(specifications, 1):
        panels.append({"id": i, "title": title, "type": kind,
                       "datasource": {"type": "loki", "uid": LOKI},
                       "targets": [{"refId": "A", "expr": expression, "queryType": "range"}],
                       "gridPos": {"x": 0, "y": (i - 1) * 8, "w": 24, "h": 8},
                       "fieldConfig": {"defaults": {"noValue": "不明"}, "overrides": []},
                       "options": {"showTime": True, "wrapLogMessage": True}})
    return {"uid": UID, "title": "OpenWrt security・offline解析", "schemaVersion": 39,
            "version": 1, "editable": False, "timezone": "browser", "refresh": "1m",
            "time": {"from": "now-6h", "to": "now"}, "panels": panels,
            "tags": [UID], "annotations": {"list": []}, "templating": {"list": []},
            "description": "Counter無効の全drop数は不明。検証用SIDの検知は通知対象外。Loki配送停止時は解析の完了と区別して調査する。"}


def add_monitoring(manifest: dict, *, paused: bool = False) -> None:
    by_name = {(x["kind"], x["metadata"]["name"]): x for x in manifest["items"]}
    cm = by_name["ConfigMap", "prod-grafana-provisioning"]
    dep = by_name["Deployment", "prod-grafana"]
    data = cm["data"]
    assert "openwrt-security.json" not in data and "security-alerting.json" not in data
    data["openwrt-security.json"] = encode(dashboard())
    data["security-alerting.json"] = encode(provisioning(paused=paused))
    provider = json.loads(data["dashboard-provider.yaml"])
    provider["providers"].append({
        "name": UID, "orgId": 1, "type": "file", "folder": "OpenWrt security",
        "folderUid": UID, "disableDeletion": True, "allowUiUpdates": False,
        "updateIntervalSeconds": 30, "options": {"path": "/etc/grafana/security-dashboards"},
    })
    data["dashboard-provider.yaml"] = encode(provider)
    template = dep["spec"]["template"]
    for name, directory, key in [
            ("security-dashboards", "/etc/grafana/security-dashboards", "openwrt-security.json"),
            ("security-alerting", "/etc/grafana/provisioning/alerting", "security-alerting.json")]:
        template["spec"]["volumes"].append({"name": name, "configMap": {
            "name": cm["metadata"]["name"], "items": [{"key": key, "path": key}]}})
        template["spec"]["containers"][0]["volumeMounts"].append({
            "name": name, "mountPath": directory, "readOnly": True})
    template["metadata"]["annotations"]["checksum/provisioning"] = hashlib.sha256(encode(data).encode()).hexdigest()
    grafana = {"app.kubernetes.io/name": "prod-grafana", "app.kubernetes.io/part-of": "prod-outside-in"}
    alertmanager = {"app.kubernetes.io/name": "prod-alertmanager", "app.kubernetes.io/part-of": "prod-outside-in"}
    by_name["NetworkPolicy", "prod-grafana-restricted"]["spec"]["egress"].append({
        "to": [{"podSelector": {"matchLabels": alertmanager}}],
        "ports": [{"protocol": "TCP", "port": 9093}]})
    manifest["items"].append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "prod-alertmanager-security-grafana", "namespace": "observability-prod"},
        "spec": {"podSelector": {"matchLabels": alertmanager}, "policyTypes": ["Ingress"],
                 "ingress": [{"from": [{"podSelector": {"matchLabels": grafana}}],
                              "ports": [{"protocol": "TCP", "port": 9093}]}]}})
