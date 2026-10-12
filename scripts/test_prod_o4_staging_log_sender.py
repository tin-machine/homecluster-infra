#!/usr/bin/env python3
"""Source-only O4 Collector opt-in and log pipeline safety contracts."""
from __future__ import annotations

import copy
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "clusters/homelab/apps/base/values-otel-collector.yaml"
STAGING = ROOT / "clusters/homelab/apps/staging/values-otel-collector.yaml"
OVERLAY = ROOT / "clusters/homelab/apps/staging/values-otel-collector-o4-prod-logs.yaml"
JOURNAL = ROOT / "clusters/homelab/apps/staging/values-fluent-bit-journal.yaml"


def merge_values(left: dict, right: dict) -> dict:
    """Model Helm's map merge and list replacement for the scoped fixture."""
    result = copy.deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_values(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class O4StagingLogSenderTests(unittest.TestCase):
    def test_overlay_sends_pod_and_openwrt_logs_from_all_four_nodes(self):
        values = {}
        for path in (BASE, STAGING, OVERLAY):
            values = merge_values(values, yaml.safe_load(path.read_text()))
        config = values["config"]
        pipelines = config["service"]["pipelines"]
        self.assertEqual(pipelines["logs"]["receivers"], ["filelog/pods"])
        self.assertEqual(pipelines["logs"]["exporters"], ["otlphttp/prod-logs"])
        self.assertEqual(pipelines["logs/openwrt"]["exporters"], ["otlphttp/prod-logs"])
        self.assertEqual(pipelines["logs/openwrt"]["receivers"], ["syslog/openwrt"])
        self.assertEqual(pipelines["logs/openwrt"]["processors"],
                         ["attributes/openwrt", "resource/environment", "batch/o4-prod-logs"])
        self.assertIsNone(values["nodeSelector"])
        self.assertEqual(values["tolerations"], [{
            "operator": "Exists", "effect": "NoSchedule"}])
        self.assertEqual(pipelines["metrics"]["exporters"], ["prometheus"])
        self.assertEqual(pipelines["traces"]["exporters"], ["otlp/tempo"])
        exporter = config["exporters"]["otlphttp/prod-logs"]
        self.assertEqual(exporter["endpoint"], "${env:PROD_LOKI_OTLP_BASE}")
        self.assertEqual(exporter["tls"]["ca_file"],
                         "/etc/otel/prod-loki-ca/ca.crt")
        self.assertNotIn("insecure", exporter["tls"])
        self.assertEqual(exporter["sending_queue"]["queue_size"], 32)
        self.assertEqual(exporter["retry_on_failure"]["max_elapsed_time"], "1m")
        names = {volume["name"] for volume in values["extraVolumes"]}
        mounts = {mount["name"] for mount in values["extraVolumeMounts"]}
        self.assertEqual(names, mounts)
        self.assertEqual(names, {"varlog", "host-proc", "host-sys",
                                 "host-run", "prod-loki-ca"})
        self.assertEqual({entry["name"] for entry in values["extraEnvs"]},
                         {"PROD_LOKI_OTLP_BASE", "PROD_LOKI_BASIC_B64"})

    def test_terraform_overlay_requires_explicit_opt_in(self):
        env = (ROOT / "terraform/env/staging/variables.tf").read_text()
        module = (ROOT / "terraform/modules/observability_stack/variables.tf").read_text()
        env_main = (ROOT / "terraform/env/staging/main.tf").read_text()
        module_main = (ROOT / "terraform/modules/observability_stack/main.tf").read_text()
        for source in (env, module):
            self.assertRegex(source, re.compile(
                r'variable "o4_prod_container_logs_enabled"\s*\{[^}]*default\s*=\s*false',
                re.S))
        self.assertIn("o4_prod_container_logs_enabled = var.o4_prod_container_logs_enabled",
                      env_main)
        self.assertIn('var.o4_prod_container_logs_enabled ? [', module_main)
        self.assertIn('file("${var.values_env_dir}/values-otel-collector-o4-prod-logs.yaml")',
                      module_main)

    def test_journal_agent_uses_local_collector_and_four_node_placement(self):
        overlay = yaml.safe_load(OVERLAY.read_text())
        pipeline = overlay["config"]["service"]["pipelines"]["logs/journal"]
        self.assertEqual(pipeline["receivers"], ["otlp"])
        self.assertEqual(pipeline["exporters"], ["otlphttp/prod-logs"])
        attributes = overlay["config"]["processors"]["resource/journal"]["attributes"]
        self.assertIn({"key": "service.name", "value": "staging-journal", "action": "upsert"},
                      attributes)
        values = yaml.safe_load(JOURNAL.read_text())
        self.assertEqual(values["tolerations"], [{"operator": "Exists", "effect": "NoSchedule"}])
        volumes = {v["name"]: v for v in values["daemonSetVolumes"]}
        self.assertEqual(volumes["journal"]["hostPath"],
                         {"path": "/run/log/journal", "type": "Directory"})
        self.assertNotIn("/var/log/containers", values["config"]["inputs"])
        self.assertIn("Name systemd", values["config"]["inputs"])
        self.assertIn("Name opentelemetry", values["config"]["outputs"])
        self.assertIn("Port 4318", values["config"]["outputs"])
        module = (ROOT / "terraform/modules/observability_stack/main.tf").read_text()
        self.assertIn('resource "helm_release" "journal_agent"', module)
        self.assertIn('count            = var.o4_prod_container_logs_enabled ? 1 : 0', module)


if __name__ == "__main__":
    unittest.main()
