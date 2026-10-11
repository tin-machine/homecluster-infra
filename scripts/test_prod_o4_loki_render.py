#!/usr/bin/env python3
"""Source-only O4 Loki backend manifest contract fixtures; no Kubernetes access."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prod_o4_loki_render as renderer


class ProdO4LokiRenderTest(unittest.TestCase):
    def setUp(self):
        self.doc = renderer.build_manifest()
        self.by_key = {(x["kind"], x["metadata"]["name"]): x
                       for x in self.doc["items"]}

    def test_additive_six_resources_only(self):
        self.assertEqual((self.doc["apiVersion"], self.doc["kind"]), ("v1", "List"))
        self.assertEqual(len(self.doc["items"]), 6)
        self.assertEqual(len(self.by_key), 6)
        self.assertEqual(set(self.by_key), {
            ("ConfigMap", "prod-loki-config"),
            ("PersistentVolumeClaim", "prod-loki-data"),
            ("Deployment", "prod-loki"),
            ("Service", "prod-loki"),
            ("NetworkPolicy", "prod-loki-restricted"),
            ("NetworkPolicy", "prod-grafana-loki-egress"),
        })
        self.assertEqual({x["metadata"]["namespace"] for x in self.doc["items"]},
                         {"observability-prod"})
        self.assertFalse(any("Secret" == x["kind"] for x in self.doc["items"]))

    def test_own_pvc_monolithic_config_retention(self):
        pvc = self.by_key[("PersistentVolumeClaim", "prod-loki-data")]
        self.assertEqual(pvc["spec"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(pvc["spec"]["storageClassName"], "local-path")
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "4Gi")
        cm = self.by_key[("ConfigMap", "prod-loki-config")]
        cfg = json.loads(cm["data"]["loki.yaml"])
        self.assertFalse(cfg["auth_enabled"])  # intentional: NEVER publicly exposed
        # Deny-all NetworkPolicy can remain strict only if ring self-talk
        # uses loopback (official Loki 3.7.x monolithic config).
        self.assertEqual(cfg["common"]["instance_addr"], "127.0.0.1")
        self.assertEqual(cfg["common"]["replication_factor"], 1)
        self.assertEqual(cfg["common"]["ring"]["kvstore"]["store"], "inmemory")
        self.assertEqual(cfg["schema_config"]["configs"][0]["store"], "tsdb")
        self.assertEqual(cfg["schema_config"]["configs"][0]["schema"], "v13")
        self.assertEqual(cfg["schema_config"]["configs"][0]["object_store"], "filesystem")
        self.assertEqual(cfg["schema_config"]["configs"][0]["index"]["period"], "24h")
        self.assertEqual(cfg["limits_config"]["retention_period"], "72h")
        self.assertTrue(cfg["limits_config"]["allow_structured_metadata"])
        self.assertTrue(cfg["compactor"]["retention_enabled"])
        self.assertEqual(cfg["compactor"]["delete_request_store"], "filesystem")
        self.assertNotIn("s3", json.dumps(cfg))
        self.assertEqual(cfg["analytics"]["reporting_enabled"], False)

    def test_single_writer_safe_pod(self):
        dep = self.by_key[("Deployment", "prod-loki")]["spec"]
        self.assertEqual(dep["replicas"], 1)
        self.assertEqual(dep["strategy"]["type"], "Recreate")
        pod = dep["template"]
        self.assertEqual(dep["selector"]["matchLabels"], pod["metadata"]["labels"])
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["spec"]["securityContext"]["fsGroup"], 10001)
        c = pod["spec"]["containers"][0]
        self.assertEqual(c["image"], "grafana/loki:3.7.8")
        self.assertTrue(c["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(c["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(c["startupProbe"]["httpGet"]["path"], "/ready")
        self.assertEqual(c["readinessProbe"]["httpGet"]["path"], "/ready")
        self.assertEqual(c["livenessProbe"]["httpGet"]["path"], "/ready")
        self.assertEqual(c["resources"]["limits"]["memory"], "768Mi")
        v = {x["name"]: x for x in pod["spec"]["volumes"]}
        self.assertEqual(v["data"]["persistentVolumeClaim"]["claimName"], "prod-loki-data")
        self.assertEqual(v["config"]["configMap"]["name"], "prod-loki-config")
        self.assertIn("tmp", v)
        config = self.by_key[("ConfigMap", "prod-loki-config")]["data"]["loki.yaml"]
        self.assertEqual(
            pod["metadata"]["annotations"]["checksum/config"],
            hashlib.sha256(config.encode()).hexdigest(),
        )

    def test_no_unauthed_public_ingress(self):
        svc = self.by_key[("Service", "prod-loki")]["spec"]
        self.assertEqual(svc["type"], "ClusterIP")
        self.assertEqual(len(svc["ports"]), 1)
        self.assertEqual(svc["ports"][0]["port"], 3100)
        ingress = self.by_key[("NetworkPolicy", "prod-loki-restricted")]["spec"]
        self.assertEqual(ingress["policyTypes"], ["Ingress", "Egress"])
        self.assertEqual(ingress["egress"], [])
        self.assertEqual(ingress["ingress"], [{
            "from": [{"podSelector": {"matchLabels": renderer._labels("prod-grafana")}}],
            "ports": [{"protocol": "TCP", "port": 3100}],
        }])
        grafana = self.by_key[("NetworkPolicy", "prod-grafana-loki-egress")]["spec"]
        self.assertEqual(grafana["policyTypes"], ["Egress"])
        self.assertEqual(grafana["podSelector"]["matchLabels"],
                         renderer._labels("prod-grafana"))
        self.assertEqual(grafana["egress"][0]["ports"], [{"protocol": "TCP", "port": 3100}])
        self.assertEqual(grafana["egress"][0]["to"][0]["podSelector"]["matchLabels"],
                         renderer._labels("prod-loki"))

    def test_deterministic_cli_and_exclusive_file(self):
        script = Path(renderer.__file__)
        first = subprocess.run([sys.executable, str(script)], check=True,
                               capture_output=True, text=True).stdout
        second = subprocess.run([sys.executable, str(script)], check=True,
                                capture_output=True, text=True).stdout
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), self.doc)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.json"
            proc = subprocess.run([sys.executable, str(script), "--output", str(output)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), first)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            output.write_text("operator-owned\n", encoding="utf-8")
            fail = subprocess.run([sys.executable, str(script), "--output", str(output)],
                                  capture_output=True, text=True)
            self.assertNotEqual(fail.returncode, 0)
            self.assertEqual(output.read_text(), "operator-owned\n")
            link = Path(temp) / "symlink.json"
            link.symlink_to(output)
            fail2 = subprocess.run([sys.executable, str(script), "--output", str(link)],
                                   capture_output=True, text=True)
            self.assertNotEqual(fail2.returncode, 0)
            self.assertEqual(output.read_text(), "operator-owned\n")


if __name__ == "__main__":
    unittest.main()
