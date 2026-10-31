import copy
import json
import unittest

from prod_grafana_terraform_input import terraform_manifest
from prod_openwrt_security_monitoring import provisioning, queries
from test_prod_grafana_prod_host import site as full_site


class SecurityMonitoringTests(unittest.TestCase):
    def full(self, enabled=False):
        return terraform_manifest(
            prometheus_site=full_site(), include_rag=True, rag_address="192.0.2.10",
            llm_address="192.0.2.23", desktop_host_address="192.0.2.11",
            typevt_host_address="192.0.2.12", include_attention=True,
            include_prod_host=True, nas_address="192.0.2.13", include_nas_syslog=True,
            include_security=enabled)

    def test_disabled_is_backward_compatible_and_boolean_required(self):
        self.assertEqual(terraform_manifest(), terraform_manifest(include_security=False))
        for value in [1, "true", None, [], {}]:
            with self.assertRaises(ValueError):
                terraform_manifest(include_security=value)
        with self.assertRaises(ValueError):
            terraform_manifest(include_security=True)

    def test_other_workloads_and_existing_dashboards_preserved(self):
        before = {x["kind"] + "/" + x["metadata"]["name"]: x for x in self.full()["items"]}
        after = {x["kind"] + "/" + x["metadata"]["name"]: x for x in self.full(True)["items"]}
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"ConfigMap/prod-grafana-provisioning", "Deployment/prod-grafana", "NetworkPolicy/prod-grafana-restricted"})
        self.assertEqual(set(after) - set(before), {"NetworkPolicy/prod-alertmanager-security-grafana"})
        a, b = before["ConfigMap/prod-grafana-provisioning"]["data"], after["ConfigMap/prod-grafana-provisioning"]["data"]
        for key in a:
            if key != "dashboard-provider.yaml":
                self.assertEqual(a[key], b[key], key)
        pa, pb = [json.loads(x["dashboard-provider.yaml"]) for x in [a, b]]
        self.assertEqual(pa["providers"], pb["providers"][:-1])

    def test_only_additive_mounts_keep_grafana_data_and_credentials(self):
        a = next(x for x in self.full()["items"] if x["metadata"]["name"] == "prod-grafana")
        b = next(x for x in self.full(True)["items"] if x["metadata"]["name"] == "prod-grafana")
        old, new = a["spec"]["template"]["spec"], copy.deepcopy(b["spec"]["template"]["spec"])
        self.assertEqual(old["volumes"], new["volumes"][:-2])
        self.assertEqual(old["containers"][0]["volumeMounts"], new["containers"][0]["volumeMounts"][:-2])
        new["volumes"] = new["volumes"][:-2]
        new["containers"][0]["volumeMounts"] = new["containers"][0]["volumeMounts"][:-2]
        self.assertEqual(old, new)

    def test_notification_policy_not_provisioned_or_secret_copied(self):
        p = provisioning()
        self.assertFalse(set(p) & {"policies", "resetPolicies", "deleteContactPoints", "deleteRules"})
        receiver = p["contactPoints"][0]["receivers"][0]
        self.assertEqual(receiver["type"], "prometheus-alertmanager")
        self.assertEqual(receiver["settings"], {"url": "http://prod-alertmanager:9093"})
        self.assertFalse(receiver["disableResolveMessage"])
        self.assertNotIn("token", json.dumps(p))

    def test_pause_keeps_owned_rules_and_contact_points(self):
        from prod_openwrt_security_monitoring import provisioning
        normal, paused = provisioning(), provisioning(paused=True)
        self.assertTrue(all(r["isPaused"] for r in paused["groups"][0]["rules"]))
        for r in paused["groups"][0]["rules"]:
            r["isPaused"] = False
        self.assertEqual(normal, paused)
        value = terraform_manifest(prometheus_site=full_site(), include_security=True,
                                   security_alerts_paused=True)
        cm = next(o for o in value["items"] if o["kind"] == "ConfigMap" and
                  o["metadata"]["name"] == "prod-grafana-provisioning")
        self.assertTrue(all(r["isPaused"] for r in json.loads(cm["data"]["security-alerting.json"])["groups"][0]["rules"]))
        with self.assertRaises(ValueError):
            terraform_manifest(security_alerts_paused=True)
        with self.assertRaises(ValueError):
            terraform_manifest(prometheus_site=full_site(), include_security=True, security_alerts_paused="true")

    def test_queries_empty_is_normal_but_query_failure_alerts(self):
        rules = provisioning()["groups"][0]["rules"]
        self.assertEqual(len({r["uid"] for r in rules}), len(rules))
        for r in rules:
            self.assertEqual(r["noDataState"], "OK")
            self.assertEqual(r["execErrState"], "Alerting")
            self.assertEqual(r["labels"]["scope"], "openwrt-security")
            self.assertEqual(r["data"][0]["datasourceUid"], "prod-loki")
        self.assertTrue(queries()["heartbeat"].startswith("absent_over_time("))
        self.assertNotIn("or vector(0)", json.dumps(rules))

    def test_dedicated_routing_mute_and_bounded_grouping(self):
        p = provisioning()
        for r in p["groups"][0]["rules"]:
            route = r["notification_settings"]
            self.assertEqual(route["receiver"], "openwrt-security")
            self.assertEqual(set(route["group_by"]), {"alertname", "grafana_folder"})
            self.assertEqual(route["mute_time_intervals"], ["openwrt-security-night"])
            self.assertEqual(route["repeat_interval"], "4h")
        self.assertEqual(p["muteTimes"][0]["time_intervals"], [{
            "times": [{"start_time": "01:00", "end_time": "06:30"}], "location": "Asia/Tokyo"}])

    def test_network_access_is_only_grafana_to_alertmanager(self):
        a, b = self.full(), self.full(True)
        old = next(x for x in a["items"] if x["metadata"]["name"] == "prod-grafana-restricted")
        new = next(x for x in b["items"] if x["metadata"]["name"] == "prod-grafana-restricted")
        self.assertEqual(old["spec"]["egress"], new["spec"]["egress"][:-1])
        self.assertEqual(new["spec"]["egress"][-1]["ports"], [{"protocol": "TCP", "port": 9093}])
        added = b["items"][-1]
        self.assertEqual(added["spec"]["policyTypes"], ["Ingress"])
        self.assertEqual(added["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"], "prod-grafana")


if __name__ == "__main__":
    unittest.main()
