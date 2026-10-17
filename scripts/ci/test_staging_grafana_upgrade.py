"""Negative fixtures for the source-level Grafana upgrade contract."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from check_staging_grafana_upgrade import (
    check_dashboards, load_config, memory_bytes, validate_contract,
)


def manifests():
    data = {"grafana.ini": "[server]\nhttp_port=3000\n", "datasources.yaml": yaml.safe_dump({
        "datasources": [{"uid": uid, "isDefault": uid == "mimir"}
                        for uid in ("mimir", "prometheus", "loki", "tempo")]})}
    env = [{"name": f"GF_SECURITY_ADMIN_{suffix}", "valueFrom": {"secretKeyRef": {
        "name": "stg-grafana-admin", "key": key}}}
        for suffix, key in (("USER", "admin-user"), ("PASSWORD", "admin-password"))]
    mounts = [{"name": "config", "subPath": name, "mountPath": path} for name, path in (
        ("grafana.ini", "/etc/grafana/grafana.ini"),
        ("datasources.yaml", "/etc/grafana/provisioning/datasources/datasources.yaml"))]
    return [
        {"kind": "Deployment", "metadata": {"name": "stg-grafana"}, "spec": {
            "replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": "grafana"}},
            "template": {"spec": {"containers": [{"name": "grafana", "env": env, "volumeMounts": mounts,
                "ports": [{"name": "grafana", "containerPort": 3000}]}],
                "volumes": [{"name": "config", "configMap": {"name": "config"}},
                            {"name": "storage", "persistentVolumeClaim": {"claimName": "stg-grafana"}}]}}}},
        {"kind": "PersistentVolumeClaim", "metadata": {"name": "stg-grafana"},
         "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "nfs-client", "resources": {"requests": {"storage": "5Gi"}}}},
        {"kind": "Service", "metadata": {"name": "stg-grafana"}, "spec": {"selector": {"app": "grafana"},
            "ports": [{"port": 80, "targetPort": 3000, "protocol": "TCP"}]}},
        {"kind": "ConfigMap", "metadata": {"name": "config"}, "data": data},
    ]


class ContractTest(unittest.TestCase):
    def test_helm_notification_matcher_scalar(self):
        self.assertEqual(load_config("object_matchers:\n- [scope, =, openwrt-security]"),
                         {"object_matchers": [["scope", "=", "openwrt-security"]]})
        with self.assertRaises(yaml.constructor.ConstructorError):
            load_config("value: !unknown-tag nope")

    def test_unchanged_contract(self):
        old = manifests()
        validate_contract(old, copy.deepcopy(old))

    def test_named_service_port_is_equivalent(self):
        old, new = manifests(), manifests()
        new[2]["spec"]["ports"][0]["targetPort"] = "grafana"
        validate_contract(old, new)
        self.assertEqual(new[2]["spec"]["ports"][0]["targetPort"], "grafana")

    def test_bad_service_port_rejected(self):
        for change in ("missing-name", "different-port", "service-selector", "external-port"):
            with self.subTest(change=change):
                old, new = manifests(), manifests()
                new[2]["spec"]["ports"][0]["targetPort"] = "grafana"
                if change == "missing-name":
                    new[2]["spec"]["ports"][0]["targetPort"] = "missing"
                elif change == "different-port":
                    new[0]["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"] = 4000
                elif change == "service-selector":
                    new[2]["spec"]["selector"] = {"app": "other"}
                else:
                    new[2]["spec"]["ports"][0]["port"] = 81
                with self.assertRaises(ValueError):
                    validate_contract(old, new)

    def test_destructive_changes_rejected(self):
        for mutation in ("replicas", "rolling", "selector", "pvc", "claim", "secret", "datasource"):
            with self.subTest(mutation=mutation):
                old, new = manifests(), manifests()
                spec = new[0]["spec"]
                if mutation == "replicas":
                    spec["replicas"] = 2
                elif mutation == "rolling":
                    spec["strategy"] = {"type": "RollingUpdate"}
                elif mutation == "selector":
                    spec["selector"] = {"matchLabels": {"app": "renamed"}}
                elif mutation == "pvc":
                    new[1]["spec"]["storageClassName"] = "different"
                elif mutation == "claim":
                    spec["template"]["spec"]["volumes"][1]["persistentVolumeClaim"]["claimName"] = "different"
                elif mutation == "secret":
                    spec["template"]["spec"]["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "different"
                else:
                    new[3]["data"]["datasources.yaml"] = "datasources: []"
                with self.assertRaises(ValueError):
                    validate_contract(old, new)

    def test_nested_angular_panel_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dashboard.json"
            path.write_text(json.dumps({"uid": "test", "title": "Test", "panels": [
                {"id": 1, "type": "row", "panels": [{"id": 2, "type": "graph"}]}]}))
            with self.assertRaisesRegex(ValueError, "Angular"):
                check_dashboards(Path(temp))

    def test_empty_dashboard_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                check_dashboards(Path(temp))

    def test_memory_limit(self):
        self.assertEqual(memory_bytes("256Mi"), 268435456)
        self.assertEqual(memory_bytes("1Gi"), 1073741824)
        with self.assertRaises(ValueError):
            memory_bytes("unlimited")


if __name__ == "__main__":
    unittest.main()
