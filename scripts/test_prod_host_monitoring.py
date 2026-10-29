import json
import unittest

from prod_grafana_terraform_input import terraform_manifest
from prod_attention_dashboard import WARN_LEVELS, dashboard as attention_dashboard
from prod_host_monitoring_dashboard import dashboard
from prod_prometheus_config_render import build_manifest as prometheus_manifest
from test_prod_outside_in_render import fixture


class HostMonitoringTests(unittest.TestCase):
    def test_adds_two_jobs_and_exact_egress_without_changing_old_jobs(self):
        old = {obj["kind"]: obj for obj in prometheus_manifest(fixture())["items"]}
        new = {obj["kind"]: obj for obj in prometheus_manifest(
            fixture(), host_addresses={"desktop": "192.0.2.23", "typevt": "100.64.0.42"})["items"]}
        old_config = json.loads(old["ConfigMap"]["data"]["prometheus.json"])
        new_config = json.loads(new["ConfigMap"]["data"]["prometheus.json"])
        self.assertEqual(old_config["scrape_configs"], new_config["scrape_configs"][:-2])
        self.assertEqual(old_config["rule_files"], new_config["rule_files"])
        self.assertEqual(old_config["alerting"], new_config["alerting"])
        jobs = new_config["scrape_configs"][-2:]
        self.assertEqual([job["job_name"] for job in jobs],
                         ["ubuntu-desktop-node-exporter", "typevt-node-exporter"])
        self.assertEqual([job["static_configs"][0]["targets"] for job in jobs],
                         [["192.0.2.23:9100"], ["100.64.0.42:9100"]])
        for job in jobs:
            self.assertEqual(job["scrape_interval"], "60s")
            self.assertEqual(job["sample_limit"], 5000)
            self.assertIn({"action": "labeldrop", "regex": "serial_number"},
                          job["metric_relabel_configs"])
        self.assertEqual(old["NetworkPolicy"]["spec"]["egress"],
                         new["NetworkPolicy"]["spec"]["egress"][:-2])
        self.assertEqual([rule["to"][0]["ipBlock"]["cidr"] for rule in
                          new["NetworkPolicy"]["spec"]["egress"][-2:]],
                         ["192.0.2.23/32", "100.64.0.42/32"])

    def test_private_address_validation_and_dashboard_missing_data(self):
        for address in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "router.local", "192.0.2.0/24"):
            with self.assertRaises(ValueError):
                prometheus_manifest(fixture(), host_addresses={"desktop": address})
        with self.assertRaises(ValueError):
            prometheus_manifest(fixture(), host_addresses={"other": "192.0.2.23"})
        with self.assertRaises(ValueError):
            terraform_manifest(desktop_host_address="192.0.2.23")
        manifest = terraform_manifest(prometheus_site=fixture(),
                                      desktop_host_address="192.0.2.23")
        self.assertEqual(len(manifest["items"]), 8)
        config = next(obj for obj in manifest["items"] if obj["kind"] == "ConfigMap"
                      and obj["metadata"]["name"] == "prod-grafana-provisioning")
        for host in ("desktop", "typevt"):
            value = dashboard(host)
            self.assertTrue(all(panel["fieldConfig"]["defaults"]["noValue"] == "未観測"
                                for panel in value["panels"]))
            self.assertIn(value["uid"] + ".json", {"ubuntu-desktop-host.json", "typevt-host.json"})
        self.assertIn("ubuntu-desktop-host.json", config["data"])
        self.assertIn("typevt-host.json", config["data"])
        tablet_queries = [panel["targets"][0]["expr"] for panel in dashboard("typevt")["panels"]]
        self.assertTrue(any("node_power_supply_energy_watthour" in query for query in tablet_queries))
        self.assertFalse(any("node_power_supply_charge_now" in query for query in tablet_queries))

    def test_attention_defaults_to_warn_and_keeps_unknown_separate(self):
        value = attention_dashboard()
        minimum = value["templating"]["list"][0]
        self.assertEqual(minimum["current"]["text"], "Warn以上")
        self.assertEqual(minimum["current"]["value"], WARN_LEVELS)
        self.assertEqual(minimum["options"][1]["value"], ".*")
        queries = [panel["targets"][0]["expr"] for panel in value["panels"]]
        self.assertIn('detected_level=~"${minimum_level:raw}"', queries[0])
        self.assertIn('detected_level=~"unknown|^$"', queries[1])
        self.assertTrue(queries[0].startswith(
            '{service_name=~".+"} | service_name=~"${service:raw}" | environment='))
        self.assertIn(' | k8s_namespace_name=~"${namespace:raw}"', queries[2])
        self.assertIn(' | _HOSTNAME=~"${host:raw}"', queries[3])
        self.assertNotIn("|~", queries[0])
        self.assertIn('service_name=~"${service:raw}"', queries[0])
        self.assertEqual(value["templating"]["list"][2]["current"]["value"], ".*")
        manifest = terraform_manifest(include_attention=True)
        config = next(obj for obj in manifest["items"] if obj["kind"] == "ConfigMap")
        self.assertIn("home-attention.json", config["data"])


if __name__ == "__main__":
    unittest.main()
