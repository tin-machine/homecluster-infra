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
HOST_JOBS = {"desktop": "ubuntu-desktop-node-exporter",
             "typevt": "typevt-node-exporter"}


def build_manifest(site: dict, *, rag_address: str = "", llm_address: str = "",
                   host_addresses: dict[str, str] | None = None) -> dict:
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
    for host, target in sorted((host_addresses or {}).items()):
        if host not in HOST_JOBS:
            raise ValueError("unexpected host monitoring target")
        address = ipaddress.IPv4Address(target)
        tailnet = address in ipaddress.IPv4Network("100.64.0.0/10")
        if (not (address.is_private or tailnet) or address.is_loopback
                or address.is_multicast or address.is_unspecified or str(address) != target):
            raise ValueError("host monitoring requires a specific private or tailnet IPv4")
        job = {"job_name": HOST_JOBS[host], "metrics_path": "/metrics", "scheme": "http",
               "scrape_interval": "60s", "scrape_timeout": "8s", "sample_limit": 5000,
               "static_configs": [{"targets": [target + ":9100"]}],
               "metric_relabel_configs": [
                   {"action": "drop", "source_labels": ["__name__"], "regex": "node_dmi_info"},
                   {"action": "labeldrop", "regex": "serial_number"},
               ]}
        config["scrape_configs"].append(job)
        policy["spec"]["egress"].append({
            "to": [{"ipBlock": {"cidr": target + "/32"}}],
            "ports": [{"protocol": "TCP", "port": 9100}],
        })
    if llm_address:
        address = ipaddress.IPv4Address(llm_address)
        tailnet = address in ipaddress.IPv4Network("100.64.0.0/10")
        if (not (address.is_private or tailnet) or address.is_loopback
                or address.is_multicast or address.is_unspecified or str(address) != llm_address):
            raise ValueError("LLM requires a specific private or tailnet IPv4")
        config["scrape_configs"].append({
            "job_name": "local-llm", "metrics_path": "/metrics", "scheme": "http",
            "scrape_interval": "15s", "scrape_timeout": "5s", "sample_limit": 200,
            "static_configs": [{"targets": [llm_address + ":8080"]}],
            "metric_relabel_configs": [{"action": "keep", "source_labels": ["__name__"],
                "regex": "llamacpp:requests_processing|llamacpp:requests_deferred"}],
        })
        policy["spec"]["egress"].append({
            "to": [{"ipBlock": {"cidr": llm_address + "/32"}}],
            "ports": [{"protocol": "TCP", "port": 8080}],
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
