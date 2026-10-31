"""Executable producer/consumer contracts for opt-in NAS monitoring."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import prod_nas_monitoring as nas
from prod_grafana_terraform_input import terraform_manifest
from test_prod_outside_in_render import fixture


def index(manifest):
    return {x["kind"] + "/" + x["metadata"]["name"]: x for x in manifest["items"]}


class NasMonitoringTests(unittest.TestCase):
    def render(self, **kwargs):
        return terraform_manifest(prometheus_site=fixture(), **kwargs)

    def test_disabled_preserves_existing_renderers(self):
        from prod_grafana_render import build_manifest
        from prod_prometheus_config_render import build_manifest as prom
        expected = build_manifest()
        expected["items"].extend(prom(fixture())["items"])
        for obj in expected["items"]:
            if obj["kind"] == "NetworkPolicy" and obj["spec"].get("ingress") == []:
                del obj["spec"]["ingress"]
        self.assertEqual(self.render(), expected)
        self.assertEqual(self.render(nas_address=""), expected)

    def test_enabled_producer_and_scrape_consumer_agree(self):
        before = index(self.render())
        after = index(self.render(nas_address="192.0.2.10", nas_username="monitor"))
        self.assertEqual(set(after) - set(before), {
            "ConfigMap/" + nas.CONFIG, "Deployment/" + nas.EXPORTER, "Service/" + nas.EXPORTER,
            "NetworkPolicy/prod-nas-snmp-restricted", "NetworkPolicy/prod-nas-prometheus-egress"})
        self.assertEqual({k for k in before if before[k] != after[k]}, {
            "ConfigMap/prod-prometheus-config", "Deployment/prod-prometheus",
            "ConfigMap/prod-grafana-provisioning", "Deployment/prod-grafana"})
        config = json.loads(after["ConfigMap/" + nas.CONFIG]["data"]["snmp.json"])
        prom = json.loads(after["ConfigMap/prod-prometheus-config"]["data"]["prometheus.json"])
        job = next(x for x in prom["scrape_configs"] if x["job_name"] == nas.JOB)
        self.assertIn(job["params"]["module"][0], config["modules"])
        auth = config["auths"][job["params"]["auth"][0]]
        self.assertEqual(auth, {"version": 3, "username": "monitor", "security_level": "noAuthNoPriv"})
        self.assertEqual(job["static_configs"], [{"targets": ["192.0.2.10:161"]}])
        self.assertEqual(job["relabel_configs"][-1]["replacement"], nas.EXPORTER + ":9116")
        self.assertEqual(job["scrape_interval"], "60s")
        self.assertEqual(job["scrape_timeout"], "30s")
        old = json.loads(before["ConfigMap/prod-prometheus-config"]["data"]["prometheus.json"])
        self.assertEqual(prom["scrape_configs"][:-1], old["scrape_configs"])
        self.assertEqual(prom["alerting"], old["alerting"])
        self.assertEqual(prom["rule_files"], old["rule_files"])

    def test_units_and_counter_semantics(self):
        cfg = nas.snmp_config()["modules"][nas.MODULE]
        metrics = {x["name"]: x for x in cfg["metrics"]}
        for name, raw, expected in [
            ("qnap_memory_free_bytes", 2048, 2097152),
            ("qnap_volume_size_bytes", 4096, 4194304),
            ("qnap_volume_free_bytes", 1024, 1048576),
            ("qnap_memory_total_bytes", 8388608, 8388608),
            ("qnap_disk_capacity_bytes", 16777216, 16777216),
            ("qnap_uptime_seconds", 12345, 123.45),
        ]:
            with self.subTest(name=name):
                self.assertEqual(metrics[name]["type"], "gauge")
                self.assertAlmostEqual(raw * metrics[name]["scale"], expected)
        counters = {x["name"] for x in cfg["metrics"] if x["type"] == "counter"}
        self.assertEqual(counters, {"qnap_network_receive_bytes_total", "qnap_network_transmit_bytes_total"})
        self.assertEqual(len(cfg["get"]), 6)
        self.assertTrue(all(x.endswith(".0") for x in cfg["get"]))
        self.assertEqual(cfg["retries"], 0)
        self.assertNotIn("1.3.6.1.4.1.24681", cfg["walk"])

    def test_network_scope_and_container_boundary(self):
        objects = index(self.render(nas_address="192.0.2.10"))
        policy = objects["NetworkPolicy/prod-nas-snmp-restricted"]["spec"]
        self.assertEqual(policy["egress"], [{"to": [{"ipBlock": {"cidr": "192.0.2.10/32"}}],
                                             "ports": [{"port": 161, "protocol": "UDP"}]}])
        self.assertEqual(policy["ingress"][0]["from"], [{"podSelector": {
            "matchLabels": nas.labels("prod-prometheus")}}])
        pod = objects["Deployment/" + nas.EXPORTER]["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertNotIn("hostNetwork", pod)
        self.assertTrue(all("hostPath" not in x for x in pod["volumes"]))
        container = pod["containers"][0]
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/")
        self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(objects["Service/" + nas.EXPORTER]["spec"]["type"], "ClusterIP")

    def test_dashboard_mount_queries_and_state_mapping(self):
        objects = index(self.render(nas_address="192.0.2.10"))
        data = objects["ConfigMap/prod-grafana-provisioning"]["data"]
        dash = json.loads(data["qnap-nas.json"])
        self.assertEqual(dash["uid"], "qnap-nas")
        providers = json.loads(data["dashboard-provider.yaml"])["providers"]
        provider = next(p for p in providers if p["name"] == "qnap-nas")
        pod = objects["Deployment/prod-grafana"]["spec"]["template"]["spec"]
        mount = next(x for x in pod["containers"][0]["volumeMounts"] if x["name"] == "nas-dashboards")
        self.assertEqual(mount["mountPath"], provider["options"]["path"])
        volume = next(x for x in pod["volumes"] if x["name"] == mount["name"])
        self.assertIn(volume["configMap"]["items"][0]["key"], data)
        status = next(x for x in dash["panels"] if x["title"] == "Disk status")
        self.assertEqual(status["fieldConfig"]["defaults"]["mappings"][0]["options"]["0"]["text"], "Ready")
        for panel in dash["panels"]:
            expr = panel["targets"][0]["expr"]
            self.assertIn('job="qnap-nas-snmp"', expr)
            self.assertNotIn("vector(0)", expr)
            if "rate(" in expr:
                self.assertIn("qnap_network_", expr)
                self.assertIn('qnap_interface_info!="lo"', expr)

    def test_username_updates_only_exporter_configuration(self):
        old = index(self.render(nas_address="192.0.2.10", nas_username="monitor"))
        new = index(self.render(nas_address="192.0.2.10", nas_username="other"))
        self.assertEqual({k for k in old if old[k] != new[k]},
                         {"ConfigMap/" + nas.CONFIG, "Deployment/" + nas.EXPORTER})

    def test_reject_invalid_inputs_and_missing_site(self):
        for address in ["8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1",
                        "255.255.255.255", "::1", "nas.invalid", "192.0.2.10:161", "192.0.2.10\n", "$(id)"]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.render(nas_address=address)
        for name in ["", "bad\nname", "$(id)", "-invalid", "x" * 33]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.render(nas_address="192.0.2.10", nas_username=name)
        with self.assertRaisesRegex(ValueError, "Prometheus site"):
            terraform_manifest(nas_address="192.0.2.10")

    def test_input_is_not_mutated(self):
        site = fixture()
        expected = copy.deepcopy(site)
        terraform_manifest(prometheus_site=site, nas_address="192.0.2.10")
        self.assertEqual(site, expected)

    def test_external_query_preserves_concurrent_llm_monitoring(self):
        old = terraform_manifest(prometheus_site=fixture(), llm_address="192.0.2.11")
        with tempfile.TemporaryDirectory(prefix="nas-llm-contract-") as directory:
            path = Path(directory) / "site.json"
            path.write_text(json.dumps(fixture()))
            path.chmod(0o600)
            query = {"prometheus_site_path": str(path), "llm_address": "192.0.2.11",
                     "nas_address": "192.0.2.10", "nas_username": "monitor"}
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("prod_grafana_terraform_input.py"))],
                input=json.dumps(query), capture_output=True, text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10, check=True)
        new = json.loads(json.loads(result.stdout)["manifest"])
        key = "ConfigMap/prod-prometheus-config"
        old_cfg = json.loads(index(old)[key]["data"]["prometheus.json"])
        new_cfg = json.loads(index(new)[key]["data"]["prometheus.json"])
        self.assertEqual(new_cfg["scrape_configs"][:-1], old_cfg["scrape_configs"])
        self.assertIn("192.0.2.11", json.dumps(new_cfg))
        self.assertEqual(new_cfg["scrape_configs"][-1]["job_name"], nas.JOB)


if __name__ == "__main__":
    unittest.main()
