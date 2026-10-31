import copy
import json
import unittest

from prod_grafana_terraform_input import terraform_manifest
from prod_openwrt_security_monitoring import provisioning, queries
from test_prod_grafana_prod_host import site as full_site

CHANNEL = "CFIXTURE1234"


class SecurityMonitoringTests(unittest.TestCase):
    def full(self, enabled=False):
        return terraform_manifest(
            prometheus_site=full_site(), include_rag=True, rag_address="192.0.2.10",
            llm_address="192.0.2.23", desktop_host_address="192.0.2.11",
            typevt_host_address="192.0.2.12", include_attention=True,
            include_prod_host=True, nas_address="192.0.2.13", include_nas_syslog=True,
            include_security=enabled, security_channel_id=CHANNEL)

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
        self.assertEqual(set(after), set(before))
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
        self.assertEqual(new["containers"][0]["env"][-3], {"name": "HOMECLUSTER_SECURITY_SLACK_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": "prod-alertmanager-slack-bot", "key": "token"}}})
        self.assertEqual(new["containers"][0]["env"][-2:], [
            {"name": "GF_PLUGINS_PREINSTALL_DISABLED", "value": "true"},
            {"name": "GF_PLUGINS_CHECK_FOR_UPDATES", "value": "false"}])
        new["containers"][0]["env"] = new["containers"][0]["env"][:-3]
        self.assertEqual(old, new)

    def test_notification_policy_not_provisioned_or_secret_copied(self):
        p = provisioning(CHANNEL)
        self.assertFalse(set(p) & {"policies", "resetPolicies", "deleteContactPoints", "deleteRules"})
        receiver = p["contactPoints"][0]["receivers"][0]
        self.assertEqual(receiver["type"], "slack")
        self.assertEqual(receiver["settings"]["recipient"], CHANNEL)
        self.assertEqual(receiver["settings"]["token"], "$HOMECLUSTER_SECURITY_SLACK_TOKEN")
        self.assertFalse(receiver["disableResolveMessage"])
        self.assertNotIn("annotations", receiver["settings"]["text"])
        self.assertNotIn("url", receiver["settings"])

    def test_pause_keeps_owned_rules_and_contact_points(self):
        from prod_openwrt_security_monitoring import provisioning
        normal, paused = provisioning(CHANNEL), provisioning(CHANNEL, paused=True)
        self.assertTrue(all(r["isPaused"] for r in paused["groups"][0]["rules"]))
        for r in paused["groups"][0]["rules"]:
            r["isPaused"] = False
        self.assertEqual(normal, paused)
        value = terraform_manifest(prometheus_site=full_site(), include_security=True,
                                   security_alerts_paused=True, security_channel_id=CHANNEL)
        cm = next(o for o in value["items"] if o["kind"] == "ConfigMap" and
                  o["metadata"]["name"] == "prod-grafana-provisioning")
        self.assertTrue(all(r["isPaused"] for r in json.loads(cm["data"]["security-alerting.json"])["groups"][0]["rules"]))
        with self.assertRaises(ValueError):
            terraform_manifest(security_alerts_paused=True)
        with self.assertRaises(ValueError):
            terraform_manifest(prometheus_site=full_site(), include_security=True, security_alerts_paused="true")

    def test_queries_empty_is_normal_but_query_failure_alerts(self):
        rules = provisioning(CHANNEL)["groups"][0]["rules"]
        self.assertEqual(len({r["uid"] for r in rules}), len(rules))
        for r in rules:
            self.assertEqual(r["noDataState"], "OK")
            self.assertEqual(r["execErrState"], "Alerting")
            self.assertEqual(r["labels"]["scope"], "openwrt-security")
            self.assertEqual(r["data"][0]["datasourceUid"], "prod-loki")
        self.assertTrue(queries()["heartbeat"].startswith("absent_over_time("))
        self.assertNotIn("or vector(0)", json.dumps(rules))

    def test_dedicated_routing_mute_and_bounded_grouping(self):
        p = provisioning(CHANNEL)
        for r in p["groups"][0]["rules"]:
            route = r["notification_settings"]
            self.assertEqual(route["receiver"], "openwrt-security")
            self.assertEqual(set(route["group_by"]), {"alertname", "grafana_folder"})
            self.assertEqual(route["mute_time_intervals"], ["openwrt-security-night"])
            self.assertEqual(route["repeat_interval"], "4h")
        self.assertEqual(p["muteTimes"][0]["time_intervals"], [{
            "times": [{"start_time": "01:00", "end_time": "06:30"}], "location": "Asia/Tokyo"}])

    def test_https_egress_excludes_lan_and_existing_policies_are_unchanged(self):
        a, b = self.full(), self.full(True)
        old = next(x for x in a["items"] if x["metadata"]["name"] == "prod-grafana-restricted")
        new = next(x for x in b["items"] if x["metadata"]["name"] == "prod-grafana-restricted")
        self.assertEqual(old["spec"]["egress"], new["spec"]["egress"][:-1])
        self.assertEqual(new["spec"]["egress"][-1]["ports"], [{"protocol": "TCP", "port": 443}])
        block = new["spec"]["egress"][-1]["to"][0]["ipBlock"]
        self.assertEqual(block["cidr"], "0.0.0.0/0")
        self.assertTrue({"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"} <= set(block["except"]))
        for value in ["", "arbitrary-channel", "C\nINJECTION", None, 1]:
            with self.assertRaises(ValueError):
                terraform_manifest(prometheus_site=full_site(), include_security=True, security_channel_id=value)


if __name__ == "__main__":
    unittest.main()
