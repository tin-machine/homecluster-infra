import json
import unittest

from prod_grafana_render import build_manifest
from prod_rag_dashboard import dashboard
from prod_prometheus_config_render import build_manifest as prometheus_manifest
from prod_grafana_terraform_input import terraform_manifest
from test_prod_outside_in_render import fixture


class RagDashboardTests(unittest.TestCase):
    def test_existing_dashboards_and_datasources_are_unchanged(self):
        before = {obj["kind"] + "/" + obj["metadata"]["name"]: obj
                  for obj in build_manifest()["items"]}
        after = {obj["kind"] + "/" + obj["metadata"]["name"]: obj
                 for obj in build_manifest(include_rag=True, folder_uid="fixture-staging")["items"]}
        self.assertEqual(set(before), set(after))
        key = "ConfigMap/prod-grafana-provisioning"
        for name, value in before[key]["data"].items():
            if name != "dashboard-provider.yaml":
                self.assertEqual(value, after[key]["data"][name])
        providers = json.loads(after[key]["data"]["dashboard-provider.yaml"])["providers"]
        self.assertEqual(providers[0]["folderUid"], "fixture-staging")
        self.assertEqual(providers[1]["folderUid"], "shared-rag")
        for name in before:
            if name not in (key, "Deployment/prod-grafana"):
                self.assertEqual(before[name], after[name])

    def test_scrape_preserves_existing_jobs_rules_and_storage(self):
        before = {o["kind"]: o for o in prometheus_manifest(fixture())["items"]}
        after = {o["kind"]: o for o in prometheus_manifest(fixture(), rag_address="192.0.2.2")["items"]}
        old = json.loads(before["ConfigMap"]["data"]["prometheus.json"])
        new = json.loads(after["ConfigMap"]["data"]["prometheus.json"])
        self.assertEqual(new["scrape_configs"][:-1], old["scrape_configs"])
        self.assertEqual(new["rule_files"], old["rule_files"])
        self.assertEqual(new["alerting"], old["alerting"])
        job = new["scrape_configs"][-1]
        self.assertEqual(job["job_name"], "desktop-rag")
        self.assertEqual(job["static_configs"], [{"targets": ["192.0.2.2:3102"]}])
        self.assertEqual(job["sample_limit"], 1000)
        egress = after["NetworkPolicy"]["spec"]["egress"]
        self.assertEqual(egress[:-1], before["NetworkPolicy"]["spec"]["egress"])
        self.assertEqual(egress[-1], {"to": [{"ipBlock": {"cidr": "192.0.2.2/32"}}],
                                     "ports": [{"protocol": "TCP", "port": 3102}]})
        for section in ("containers", "volumes", "securityContext"):
            self.assertEqual(before["Deployment"]["spec"]["template"]["spec"][section],
                             after["Deployment"]["spec"]["template"]["spec"][section])
        manifest = terraform_manifest(include_rag=True, prometheus_site=fixture(), rag_address="192.0.2.2")
        self.assertEqual(len(manifest["items"]), 8)
        self.assertFalse(any(o["kind"] in ("Secret", "PersistentVolume", "PersistentVolumeClaim")
                             for o in manifest["items"]))
        for address in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "192.0.2.0/24", "router.local"):
            with self.assertRaises(ValueError):
                prometheus_manifest(fixture(), rag_address=address)
        with self.assertRaises(ValueError):
            terraform_manifest(include_rag=True)

    def test_four_sections_and_missing_data_semantics(self):
        value = dashboard()
        self.assertEqual([p["title"] for p in value["panels"] if p["type"] == "row"],
                         ["稼働", "索引", "検索", "資源"])
        panels = [p for p in value["panels"] if p["type"] != "row"]
        for panel in panels:
            self.assertEqual(panel["datasource"]["uid"], "prod-prometheus")
            self.assertEqual(panel["fieldConfig"]["defaults"]["noValue"], "未観測")
            for target in panel["targets"]:
                self.assertIn('job="desktop-rag"', target["expr"])
                if "histogram_quantile" in target["expr"]:
                    self.assertIn("duration_seconds_count", target["expr"])
                    self.assertIn("> 0", target["expr"])


if __name__ == "__main__":
    unittest.main()
