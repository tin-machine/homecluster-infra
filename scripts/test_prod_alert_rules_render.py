#!/usr/bin/env python3
"""Deterministic source-only O3 alert rule contract, no live mutation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import prod_alert_rules_render as alert
import prod_outside_in_render as o1
import prod_grafana_render as grafana


class AlertEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = alert.build_manifest()
        self.items = self.manifest["items"]
        self.resource = self.items[0]
        self.rules = json.loads(self.resource["data"][alert.RULE_KEY])
        self.alerts = {r["alert"]: r for r in self.rules["groups"][0]["rules"]}

    def test_configmap_only_no_o1_o2_mutations(self):
        self.assertEqual(self.manifest["apiVersion"], "v1")
        self.assertEqual(self.manifest["kind"], "List")
        self.assertEqual(len(self.items), 1)
        self.assertEqual(self.resource["kind"], "ConfigMap")
        self.assertEqual(self.resource["metadata"], {
            "name": alert.RULE_CONFIGMAP, "namespace": o1.NAMESPACE,
        })
        original = {(item["kind"], item["metadata"]["name"])
                    for item in o1.build_manifest({
                        "api_targets": [{"name":"api","target":"https://example.invalid:6443/readyz"}],
                        "tcp_targets": [], "egress_cidrs": ["198.51.100.0/24"],
                        "api_ca_secret": None,
                    })["items"]}
        existing_grafana = {(item["kind"], item["metadata"]["name"])
                            for item in grafana.build_manifest()["items"]}
        self.assertNotIn(("ConfigMap", alert.RULE_CONFIGMAP), original | existing_grafana)
        self.assertEqual(self.manifest, alert.build_manifest())

    def test_exact_seven_distinct_alerts_and_bounded_cardinality(self):
        expected = {
            "StagingAPIProbeFailed",
            "StagingAPIBlackboxScrapeFailed",
            "StagingAPIProbeTargetsMissing",
            "StagingTCPProbeFailed",
            "StagingTCPBlackboxScrapeFailed",
            "StagingNodeExporterScrapeFailed",
            "StagingNodeExporterTargetsMissing",
        }
        self.assertEqual(set(self.alerts), expected)
        self.assertEqual(len(self.alerts), 7)
        self.assertEqual(self.rules["groups"][0]["name"], "staging-outside-in")
        self.assertEqual(self.rules["groups"][0]["interval"], "30s")
        for name, rule in self.alerts.items():
            with self.subTest(name=name):
                self.assertRegex(rule["for"], r"^[235]m$")
                self.assertEqual(rule["labels"]["scope"], "staging")
                self.assertEqual(rule["labels"]["source"], "prod-outside-in")
                self.assertIn(rule["labels"]["severity"], ("warning", "critical"))
                self.assertNotIn("url", rule)
                self.assertEqual(set(rule["annotations"]), {"summary"})

    def test_probe_failure_differs_from_blackbox_scrape_failure(self):
        self.assertEqual(self.alerts["StagingAPIProbeFailed"]["expr"],
                         'probe_success{job="staging-api"} == 0')
        self.assertEqual(self.alerts["StagingAPIBlackboxScrapeFailed"]["expr"],
                         'up{job="staging-api"} == 0')
        self.assertEqual(self.alerts["StagingTCPProbeFailed"]["expr"],
                         'probe_success{job="staging-tcp"} == 0')
        self.assertEqual(self.alerts["StagingTCPBlackboxScrapeFailed"]["expr"],
                         'up{job="staging-tcp"} == 0')
        self.assertEqual(self.alerts["StagingNodeExporterScrapeFailed"]["expr"],
                         'up{job="staging-node-exporter"} == 0')

    def test_missing_series_has_explicit_or_vector_zero_fallback(self):
        self.assertEqual(self.alerts["StagingAPIProbeTargetsMissing"]["expr"],
                         '(count(up{job="staging-api"}) or vector(0)) < 1')
        self.assertEqual(self.alerts["StagingNodeExporterTargetsMissing"]["expr"],
                         '(count(up{job="staging-node-exporter"}) or vector(0)) < 4')
        self.assertEqual(alert.EXPECTED_STAGING_NODES, 4)
        # Avoid "up == 0" as the sole signal: missing targets produce no series.
        self.assertTrue(all("or vector(0)" in self.alerts[name]["expr"]
                            for name in ("StagingAPIProbeTargetsMissing",
                                         "StagingNodeExporterTargetsMissing")))

    def test_no_private_target_or_notification_receiver_material(self):
        content = json.dumps(self.manifest, sort_keys=True)
        for forbidden in (
            "Bearer ", "webhook_url", "slack_api_url",
            "alertmanager", "secretKeyRef", "kubeconfig", "receiver", "externalLabels",
        ):
            self.assertNotIn(forbidden, content)
        self.assertEqual(set(self.resource["data"]), {alert.RULE_KEY})
        self.assertTrue(alert.RULE_PATH.endswith("/alerts.json"))

    def test_stable_output_and_no_overwrite(self):
        script = Path(alert.__file__)
        expected = (alert._canonical(alert.build_manifest()) + "\n").encode()
        result = subprocess.run([sys.executable, str(script)], capture_output=True, check=True)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(hashlib.sha256(result.stdout).hexdigest(),
                         hashlib.sha256(expected).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "alerts.json"
            result = subprocess.run([sys.executable, str(script), "--output", str(target)],
                                    capture_output=True, check=True)
            self.assertEqual(target.read_bytes(), expected)
            duplicate = subprocess.run([sys.executable, str(script), "--output", str(target)],
                                       capture_output=True)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(target.read_bytes(), expected)


    def test_promtool_synthetic_evaluation(self):
        """Execute the actual Prometheus 3.14 rule engine on isolated time series.

        CI must run this, not silently skip if Docker is unavailable. No cluster
        credentials, namespace or network are exposed to the evaluation.
        """
        fixture = Path(alert.__file__).parent / "fixtures/prod_o3_promtool_test.json"
        test_spec = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(test_spec["rule_files"], ["alerts.json"])
        self.assertGreaterEqual(len(test_spec["tests"]), 12)
        with tempfile.TemporaryDirectory(prefix="prod-o3-promtool-") as tmp:
            work = Path(tmp)
            os.chmod(work, 0o755)  # nobody inside the isolated container
            (work / "alerts.json").write_text(
                json.dumps(self.rules, sort_keys=True) + "\\n", encoding="utf-8")
            shutil.copyfile(fixture, work / "fixture.json")
            if shutil.which("docker"):
                command = [
                    "docker", "run", "--rm", "--network=none", "--read-only",
                    "--cap-drop=ALL", "--security-opt=no-new-privileges",
                    "--user=65534:65534",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                    "--mount", f"type=bind,source={work},target=/work,readonly",
                    "--workdir", "/work", "--entrypoint", "/bin/promtool",
                    "quay.io/prometheus/prometheus:v3.14.0",
                ]
            elif shutil.which("promtool") and not os.getenv("CI"):
                command = ["promtool"]
            elif os.getenv("CI"):
                self.fail("CI requires Docker to execute pinned Prometheus promtool fixtures")
            else:
                self.skipTest("Docker/promtool unavailable for local semantic evaluation")
            for action in (["check", "rules", "alerts.json"],
                           ["test", "rules", "fixture.json"]):
                with self.subTest(action=action):
                    proc = subprocess.run(
                        command + action, cwd=work, capture_output=True,
                        text=True, timeout=240, check=False)
                    self.assertEqual(proc.returncode, 0,
                                     f"promtool {' '.join(action)} failed:\\n{proc.stdout}\\n{proc.stderr}")



if __name__ == "__main__":
    unittest.main()
