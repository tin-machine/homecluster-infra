#!/usr/bin/env python3
"""Source-only tests for the additive production Grafana dashboard design."""
from __future__ import annotations

import hashlib
import json
import unittest

import prod_grafana_render as grafana
import prod_outside_in_render as o1


def index(manifest: dict) -> dict:
    return {(obj["kind"], obj["metadata"]["name"]): obj for obj in manifest["items"]}


class GrafanaSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = grafana.build_manifest()
        self.items = index(self.manifest)

    def test_five_additive_resources_and_no_o1_updates(self) -> None:
        self.assertEqual(self.manifest["kind"], "List")
        self.assertEqual(len(self.items), 5)
        self.assertEqual(set(self.items), {
            ("ConfigMap", grafana.CONFIG_NAME), ("Deployment", grafana.GRAFANA),
            ("Service", grafana.GRAFANA),
            ("NetworkPolicy", "prod-grafana-restricted"),
            ("NetworkPolicy", "prod-prometheus-grafana-ingress"),
        })
        self.assertEqual(set(self.items).intersection(o1_expected()), set())
        for obj in self.items.values():
            self.assertEqual(obj["metadata"]["namespace"], o1.NAMESPACE)
        self.assertEqual(self.manifest, grafana.build_manifest())

    def test_grafana_secret_only_from_private_secret_ref(self) -> None:
        deployment = self.items[("Deployment", grafana.GRAFANA)]
        spec = deployment["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["securityContext"]["runAsUser"], 472)
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})
        container = spec["containers"][0]
        self.assertEqual(container["image"], grafana.IMAGE)
        self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        env = {x["name"]: x for x in container["env"]}
        for name, key in (
            ("GF_SECURITY_ADMIN_PASSWORD", "admin-password"),
            ("GF_SECURITY_SECRET_KEY", "secret-key"),
        ):
            self.assertEqual(env[name]["valueFrom"]["secretKeyRef"],
                             {"name": grafana.ADMIN_SECRET, "key": key})
            self.assertNotIn("value", env[name])
        self.assertEqual(env["GF_AUTH_ANONYMOUS_ENABLED"]["value"], "false")
        self.assertEqual(env["GF_USERS_ALLOW_SIGN_UP"]["value"], "false")
        self.assertNotIn(("Secret", grafana.ADMIN_SECRET), self.items)
        self.assertNotIn("admin/admin", json.dumps(self.manifest))

    def test_dashboard_uses_real_node_metrics_and_existing_probes(self) -> None:
        cm = self.items[("ConfigMap", grafana.CONFIG_NAME)]
        data = cm["data"]
        self.assertEqual(set(data), {"datasource.yaml", "dashboard-provider.yaml",
                                     "staging-overview.json"})
        ds = json.loads(data["datasource.yaml"])["datasources"][0]
        self.assertEqual(ds["url"], "http://prod-prometheus:9090")
        self.assertEqual(ds["uid"], grafana.DATASOURCE_UID)
        self.assertFalse(ds["editable"])
        provider = json.loads(data["dashboard-provider.yaml"])["providers"][0]
        self.assertFalse(provider["allowUiUpdates"])
        self.assertTrue(provider["disableDeletion"])
        dashboard = json.loads(data["staging-overview.json"])
        self.assertEqual(dashboard["uid"], grafana.DASHBOARD_UID)
        self.assertEqual(len(dashboard["panels"]), 8)
        queries = "\n".join(p["targets"][0]["expr"] for p in dashboard["panels"])
        for metric in (
            "up{job=", "node_cpu_seconds_total", "node_memory_MemTotal_bytes",
            "node_memory_MemAvailable_bytes", "node_filesystem_size_bytes",
            "node_filesystem_avail_bytes", "node_boot_time_seconds",
            "probe_success{job=\"staging-api\"}",
            "probe_success{job=\"staging-tcp\"}",
        ):
            self.assertIn(metric, queries)
        self.assertIn('mountpoint="/"', queries)
        self.assertEqual(len({p["id"] for p in dashboard["panels"]}), 8)

    def test_checksum_rollout_and_ephemeral_database(self) -> None:
        dep = self.items[("Deployment", grafana.GRAFANA)]
        cm = self.items[("ConfigMap", grafana.CONFIG_NAME)]["data"]
        expected = hashlib.sha256(grafana._json(cm).encode("utf-8")).hexdigest()
        self.assertEqual(
            dep["spec"]["template"]["metadata"]["annotations"]["checksum/provisioning"],
            expected,
        )
        volumes = {v["name"]: v for v in dep["spec"]["template"]["spec"]["volumes"]}
        self.assertEqual(set(volumes), {"datasources", "providers", "dashboards", "data", "temp"})
        self.assertEqual(volumes["data"]["emptyDir"]["sizeLimit"], "256Mi")
        self.assertEqual(volumes["temp"]["emptyDir"]["sizeLimit"], "64Mi")
        self.assertTrue(all("persistentVolumeClaim" not in v for v in volumes.values()))
        self.assertEqual(volumes["datasources"]["configMap"]["name"], grafana.CONFIG_NAME)
        self.assertEqual(volumes["providers"]["configMap"]["name"], grafana.CONFIG_NAME)
        self.assertEqual(volumes["dashboards"]["configMap"]["name"], grafana.CONFIG_NAME)

    def test_no_external_service_and_additive_tight_network_scope(self) -> None:
        service = self.items[("Service", grafana.GRAFANA)]
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(service["spec"]["ports"][0]["port"], 3000)
        self.assertNotIn("nodePort", json.dumps(service))
        grafana_policy = self.items[("NetworkPolicy", "prod-grafana-restricted")]["spec"]
        self.assertEqual(grafana_policy["policyTypes"], ["Ingress", "Egress"])
        self.assertEqual(grafana_policy["ingress"], [])
        self.assertEqual(len(grafana_policy["egress"]), 2)
        self.assertEqual(grafana_policy["egress"][1], {
            "to": [{"podSelector": {"matchLabels": grafana._labels(grafana.PROMETHEUS)}}],
            "ports": [{"protocol": "TCP", "port": 9090}],
        })
        prom_policy = self.items[("NetworkPolicy", "prod-prometheus-grafana-ingress")]["spec"]
        self.assertEqual(prom_policy["policyTypes"], ["Ingress"])
        self.assertEqual(prom_policy["podSelector"]["matchLabels"],
                         grafana._labels(grafana.PROMETHEUS))
        self.assertEqual(prom_policy["ingress"], [{
            "from": [{"podSelector": {"matchLabels": grafana._labels(grafana.GRAFANA)}}],
            "ports": [{"protocol": "TCP", "port": 9090}],
        }])
        self.assertNotIn("egress", prom_policy)


def o1_expected() -> set:
    return {
        ("Namespace", o1.NAMESPACE),
        ("ConfigMap", "prod-blackbox-config"),
        ("ConfigMap", "prod-prometheus-config"),
        ("PersistentVolumeClaim", "prod-prometheus-data"),
        ("Deployment", "prod-blackbox-exporter"),
        ("Deployment", "prod-prometheus"),
        ("Service", "prod-blackbox-exporter"),
        ("Service", "prod-prometheus"),
        ("NetworkPolicy", "prod-blackbox-restricted"),
        ("NetworkPolicy", "prod-prometheus-restricted"),
    }


if __name__ == "__main__":
    unittest.main()
