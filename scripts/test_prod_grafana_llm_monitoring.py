import json
import unittest
from prod_prometheus_config_render import build_manifest
from prod_grafana_terraform_input import terraform_manifest
from test_prod_outside_in_render import fixture

class LlmMonitoringTests(unittest.TestCase):
    def test_opt_in_only_changes_three_prometheus_objects(self):
        before=build_manifest(fixture())
        after=build_manifest(fixture(),llm_address="192.0.2.23")
        self.assertEqual(len(after["items"]),3)
        old={o["kind"]:o for o in before["items"]};new={o["kind"]:o for o in after["items"]}
        a=json.loads(old["ConfigMap"]["data"]["prometheus.json"])
        b=json.loads(new["ConfigMap"]["data"]["prometheus.json"])
        self.assertEqual(b["scrape_configs"][:-1],a["scrape_configs"])
        self.assertEqual(b["rule_files"],a["rule_files"]);self.assertEqual(b["alerting"],a["alerting"])
        job=b["scrape_configs"][-1]
        self.assertEqual(job["job_name"],"local-llm")
        self.assertEqual(job["static_configs"],[{"targets":["192.0.2.23:8080"]}])
        self.assertEqual(job["scrape_interval"],"15s");self.assertEqual(job["scrape_timeout"],"5s")
        self.assertEqual(job["sample_limit"],200)
        self.assertEqual(job["metric_relabel_configs"],[{"action":"keep","source_labels":["__name__"],"regex":"llamacpp:requests_processing|llamacpp:requests_deferred"}])
        self.assertEqual(new["NetworkPolicy"]["spec"]["egress"][:-1],old["NetworkPolicy"]["spec"]["egress"])
        self.assertEqual(new["NetworkPolicy"]["spec"]["egress"][-1],{"to":[{"ipBlock":{"cidr":"192.0.2.23/32"}}],"ports":[{"protocol":"TCP","port":8080}]})
        self.assertEqual(new["Deployment"]["spec"]["template"]["spec"],old["Deployment"]["spec"]["template"]["spec"])
        self.assertNotEqual(new["Deployment"]["spec"]["template"]["metadata"],old["Deployment"]["spec"]["template"]["metadata"])
        manifest=terraform_manifest(prometheus_site=fixture(),llm_address="192.0.2.23")
        self.assertEqual(len(manifest["items"]),8)
    def test_disabled_is_identical_and_invalid_targets_rejected(self):
        self.assertEqual(build_manifest(fixture()),build_manifest(fixture(),llm_address=""))
        build_manifest(fixture(),llm_address="100.100.1.1")
        for value in ["8.8.8.8","127.0.0.1","0.0.0.0","224.0.0.1","10.0.0.0/24","host.local"]:
            with self.assertRaises(ValueError):build_manifest(fixture(),llm_address=value)

    def test_llm_address_requires_prometheus_site(self):
        with self.assertRaises(ValueError):
            terraform_manifest(llm_address="192.0.2.23")

if __name__=="__main__":unittest.main()
