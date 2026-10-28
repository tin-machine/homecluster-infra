"""Desired configuration for the existing O1/O2/O3/O3B Prometheus deployment.

Own only configuration, deployment and egress. Storage, rules, credentials and
Alertmanager resources remain with their existing owners. No live reads.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json

import prod_alert_rules_render as rules
import prod_outside_in_render as outside

IDENTITIES = {"ConfigMap/prod-prometheus-config", "Deployment/prod-prometheus",
              "NetworkPolicy/prod-prometheus-restricted"}


def build_manifest(site: dict, *, rag_address: str = "") -> dict:
    source = outside.build_manifest(site)
    items = [x for x in source["items"]
             if f'{x["kind"]}/{x["metadata"]["name"]}' in IDENTITIES]
    cm, deployment, policy = (next(x for x in items if x["kind"] == kind)
                              for kind in ("ConfigMap", "Deployment", "NetworkPolicy"))
    config = json.loads(cm["data"]["prometheus.json"])
    config["rule_files"] = [rules.RULE_PATH]
    config["alerting"] = {"alertmanagers": [{"static_configs": [{
        "targets": ["prod-alertmanager:9093"]}]}]}
    # O3B's separate egress policy already grants access to Alertmanager.
    if rag_address:
        address = ipaddress.IPv4Address(rag_address)
        if (not address.is_private or address.is_loopback or address.is_multicast
                or address.is_unspecified or str(address) != rag_address):
            raise ValueError("RAG requires a specific private IPv4 address")
        config["scrape_configs"].append({
            "job_name": "desktop-rag", "metrics_path": "/metrics", "scheme": "http",
            "scrape_interval": "30s", "scrape_timeout": "5s", "sample_limit": 1000,
            "static_configs": [{"targets": [rag_address + ":3102"]}],
            "metric_relabel_configs": [{"action": "keep", "source_labels": ["__name__"],
                                        "regex": "rag_.*|process_start_time_seconds"}],
        })
        policy["spec"]["egress"].append({
            "to": [{"ipBlock": {"cidr": rag_address + "/32"}}],
            "ports": [{"protocol": "TCP", "port": 3102}],
        })
    config_text = json.dumps(config, sort_keys=True)
    cm["data"]["prometheus.json"] = config_text
    rule_text = rules.build_manifest()["items"][0]["data"][rules.RULE_KEY]
    template = deployment["spec"]["template"]
    template["metadata"]["annotations"]["checksum/config"] = hashlib.sha256(
        (config_text + "\n" + rule_text).encode()).hexdigest()
    template["spec"]["volumes"].append({
        "name": "alert-rules", "configMap": {"name": rules.RULE_CONFIGMAP}})
    template["spec"]["containers"][0]["volumeMounts"].append({
        "name": "alert-rules", "mountPath": rules.RULE_MOUNT, "readOnly": True})
    return {"apiVersion": "v1", "kind": "List", "items": items}
