"""Offline regression tests for the pinned-image Collector chart migration."""
import copy
import unittest

import yaml

from check_staging_otel_upgrade import IMAGE, active_config, contract


def fixture():
    cfg = {
        "receivers": {"otlp": {}, "filelog/pods": {"include": ["/var/log/pods/*/*/*.log"]}},
        "processors": {"batch": {}, "resource/environment": {"attributes": [{"key": "environment", "value": "staging", "action": "upsert"}]}},
        "exporters": {"otlp/tempo": {"endpoint": "tempo:4317"}, "otlphttp/prod-logs": {
            "endpoint": "${env:PROD_LOKI_OTLP_BASE}", "tls": {"ca_file": "/etc/otel/prod-loki-ca/ca.crt"},
            "headers": {"Authorization": "Basic ${env:PROD_LOKI_BASIC_B64}"}}, "prometheus": {"endpoint": "0.0.0.0:8889"}},
        "extensions": {"health_check": {"endpoint": "${env:MY_POD_IP}:13133"}},
        "service": {"telemetry": {"metrics": {"address": "${env:MY_POD_IP}:8888"}}, "extensions": ["health_check"], "pipelines": {
            "logs": {"receivers": ["filelog/pods"], "processors": ["resource/environment", "batch"], "exporters": ["otlphttp/prod-logs"]},
            "traces": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["otlp/tempo"]},
            "metrics": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["prometheus"]}}}}
    pod = {"serviceAccountName": "collector", "containers": [{"image": IMAGE, "ports": [{"name": "metrics", "containerPort": 8889}],
           "env": [{"name": "PROD_LOKI_BASIC_B64", "valueFrom": {"secretKeyRef": {"name": "staging-prod-loki-push", "key": "basic_authorization_b64"}}}]}],
           "volumes": [{"name": "varlog", "hostPath": {"path": "/var/log"}}]}
    return [{"kind": "DaemonSet", "metadata": {"name": "collector-agent"}, "spec": {"selector": {"matchLabels": {"app": "collector"}},
            "template": {"spec": pod}}},
            {"kind": "ConfigMap", "metadata": {"name": "collector-agent"}, "data": {"relay": yaml.safe_dump(cfg)}},
            {"kind": "Service", "metadata": {"name": "collector"}, "spec": {"selector": {"app": "collector"},
                "ports": [{"name": "metrics", "port": 8889, "targetPort": 8889}]}},
            {"kind": "ServiceAccount", "metadata": {"name": "collector"}}]


class CollectorChartTest(unittest.TestCase):
    def test_unchanged_contract_and_no_mutation(self):
        before, after = fixture(), fixture()
        preserved = copy.deepcopy(after)
        contract(before, after)
        self.assertEqual(after, preserved)

    def test_only_unused_definitions_are_ignored(self):
        a = yaml.safe_load(fixture()[1]["data"]["relay"])
        b = copy.deepcopy(a)
        b["processors"]["memory_limiter"] = {"limit_percentage": 80}
        self.assertEqual(active_config(a), active_config(b))
        b["service"]["pipelines"]["logs"]["processors"].insert(0, "memory_limiter")
        self.assertNotEqual(active_config(a), active_config(b))

    def test_unresolved_components_rejected(self):
        for category in ("receivers", "processors", "exporters"):
            with self.subTest(category=category):
                cfg = yaml.safe_load(fixture()[1]["data"]["relay"])
                cfg["service"]["pipelines"]["logs"][category] = ["missing"]
                with self.assertRaises(ValueError):
                    active_config(cfg)

    def test_routing_credentials_and_collector_config_changes_rejected(self):
        for change in ("endpoint", "ca", "auth", "rename", "pipeline", "telemetry", "labels"):
            with self.subTest(change=change):
                old, new = fixture(), fixture()
                cfg = yaml.safe_load(new[1]["data"]["relay"])
                exporter = cfg["exporters"]["otlphttp/prod-logs"]
                if change == "endpoint":
                    exporter["endpoint"] = "https://wrong.invalid"
                elif change == "ca":
                    exporter["tls"] = {"insecure_skip_verify": True}
                elif change == "auth":
                    exporter["headers"] = {}
                elif change == "rename":
                    cfg["exporters"]["otlp_http/prod-logs"] = cfg["exporters"].pop("otlphttp/prod-logs")
                elif change == "pipeline":
                    cfg["service"]["pipelines"].pop("logs")
                elif change == "telemetry":
                    cfg["service"]["telemetry"]["metrics"] = {"readers": []}
                else:
                    cfg["processors"]["resource/environment"]["attributes"][0]["value"] = "production"
                new[1]["data"]["relay"] = yaml.safe_dump(cfg)
                with self.assertRaises(ValueError):
                    contract(old, new)

    def test_workload_pin_mount_service_and_secret_changes_rejected(self):
        for change in ("image", "selector", "mount", "secret", "port", "resources", "new-env"):
            with self.subTest(change=change):
                old, new = fixture(), fixture()
                pod = new[0]["spec"]["template"]["spec"]
                container = pod["containers"][0]
                if change == "image":
                    container["image"] = IMAGE.replace("0.102.1", "0.161.0")
                elif change == "selector":
                    new[0]["spec"]["selector"] = {"matchLabels": {"app": "other"}}
                elif change == "mount":
                    pod["volumes"][0]["hostPath"]["path"] = "/other"
                elif change == "secret":
                    container["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "other"
                elif change == "port":
                    new[2]["spec"]["ports"][0]["port"] = 9999
                elif change == "resources":
                    container["resources"] = {"limits": {"memory": "1Gi"}}
                else:
                    container["env"].append({"name": "UNEXPECTED", "value": "changed"})
                with self.assertRaises(ValueError):
                    contract(old, new)

    def test_app_protocol_hint_allowed(self):
        old, new = fixture(), fixture()
        new[2]["spec"]["ports"][0]["appProtocol"] = "http"
        contract(old, new)


if __name__ == "__main__":
    unittest.main()
