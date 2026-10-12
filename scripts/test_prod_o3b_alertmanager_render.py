#!/usr/bin/env python3
"""Source-only O3-B Alertmanager manifest contract tests."""
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
import prod_o3b_alertmanager_render as renderer

CHANNEL = "C0123456789"


class ProdO3BAlertmanagerRenderTest(unittest.TestCase):
    def setUp(self):
        self.doc = renderer.build_manifest(CHANNEL)
        self.by_key = {(x["kind"], x["metadata"]["name"]): x
                       for x in self.doc["items"]}

    def test_five_additive_resources_no_secret_or_prometheus_mutation(self):
        self.assertEqual(set(self.by_key), {
            ("ConfigMap", "prod-alertmanager-config"),
            ("Deployment", "prod-alertmanager"),
            ("Service", "prod-alertmanager"),
            ("NetworkPolicy", "prod-alertmanager-restricted"),
            ("NetworkPolicy", "prod-prometheus-alertmanager-egress"),
        })
        self.assertEqual(len(self.doc["items"]), 5)
        self.assertEqual({x["metadata"]["namespace"] for x in self.doc["items"]},
                         {"observability-prod"})

    def test_bot_token_is_only_a_mounted_secret_reference(self):
        cm = self.by_key[("ConfigMap", "prod-alertmanager-config")]
        cfg = json.loads(cm["data"]["alertmanager.json"])
        slack = cfg["receivers"][0]["slack_configs"][0]
        self.assertEqual(slack["api_url"], "https://slack.com/api/chat.postMessage")
        self.assertEqual(slack["channel"], CHANNEL)
        self.assertTrue(slack["send_resolved"])
        self.assertEqual(slack["http_config"]["authorization"], {
            "type": "Bearer", "credentials_file": "/etc/alertmanager-secret/token",
        })
        self.assertEqual(cfg["route"]["group_by"], ["alertname"])
        self.assertEqual(cfg["route"]["group_wait"], "30s")
        self.assertEqual(cfg["route"]["group_interval"], "5m")
        self.assertEqual(cfg["route"]["repeat_interval"], "4h")
        rendered = cm["data"]["alertmanager.json"]
        for forbidden in ("xoxb-", "hooks.slack.com", ".Annotations", ".Labels.instance",
                          "GeneratorURL", "title_link\":\"http"):
            self.assertNotIn(forbidden, rendered)
        volumes = self.by_key[("Deployment", "prod-alertmanager")]["spec"]["template"]["spec"]["volumes"]
        secret = next(v for v in volumes if v["name"] == "slack-token")
        self.assertEqual(secret["secret"]["secretName"], "prod-alertmanager-slack-bot")
        self.assertEqual(secret["secret"]["items"], [{"key": "token", "path": "token"}])

    def test_single_instance_health_and_network_boundary(self):
        dep = self.by_key[("Deployment", "prod-alertmanager")]["spec"]
        self.assertEqual(dep["replicas"], 1)
        self.assertEqual(dep["strategy"]["type"], "Recreate")
        pod = dep["template"]
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        container = pod["spec"]["containers"][0]
        self.assertEqual(container["image"], renderer.IMAGE)
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/-/ready")
        config = self.by_key[("ConfigMap", "prod-alertmanager-config")]["data"]["alertmanager.json"]
        self.assertEqual(pod["metadata"]["annotations"]["checksum/config"],
                         hashlib.sha256(config.encode()).hexdigest())
        svc = self.by_key[("Service", "prod-alertmanager")]["spec"]
        self.assertEqual(svc["type"], "ClusterIP")
        self.assertEqual(svc["ports"][0]["port"], 9093)
        ingress = self.by_key[("NetworkPolicy", "prod-alertmanager-restricted")]["spec"]
        self.assertEqual(ingress["ingress"][0]["from"][0]["podSelector"]["matchLabels"],
                         renderer._labels("prod-prometheus"))
        self.assertEqual(ingress["egress"][1]["ports"],
                         [{"protocol": "TCP", "port": 443}])
        prom = self.by_key[("NetworkPolicy", "prod-prometheus-alertmanager-egress")]["spec"]
        self.assertEqual(prom["egress"][0]["ports"],
                         [{"protocol": "TCP", "port": 9093}])

    def test_private_site_and_output_contract(self):
        script = Path(renderer.__file__)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            site = folder / "site.json"
            output = folder / "manifest.json"
            site.write_text(json.dumps({"slack_channel_id": CHANNEL}))
            site.chmod(0o600)
            call = [sys.executable, str(script), "--site", str(site),
                    "--output", str(output)]
            ok = subprocess.run(call, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stdout, "")
            self.assertEqual(json.loads(output.read_text()), self.doc)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            again = subprocess.run(call, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            site.chmod(0o644)
            unsafe = subprocess.run([*call[:-1], str(folder / "other.json")],
                                    capture_output=True, text=True)
            self.assertNotEqual(unsafe.returncode, 0)
            site.chmod(0o600)
            site.write_text('{"slack_channel_id":"C0123456789","slack_channel_id":"C9876543210"}')
            duplicate = subprocess.run([*call[:-1], str(folder / "duplicate.json")],
                                       capture_output=True, text=True)
            self.assertNotEqual(duplicate.returncode, 0)
            link = folder / "link.json"
            link.symlink_to(site)
            linked = subprocess.run([sys.executable, str(script), "--site", str(link),
                                     "--output", str(folder / "linked.json")],
                                    capture_output=True, text=True)
            self.assertNotEqual(linked.returncode, 0)
            self.assertFalse((folder / "linked.json").exists())

    def test_channel_validation(self):
        for bad in ("#alerts", "C", "xoxb-123", "C0123456789\nother", 123):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    renderer.build_manifest(bad)


if __name__ == "__main__":
    unittest.main()
