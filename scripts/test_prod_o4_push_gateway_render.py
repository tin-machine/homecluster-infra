#!/usr/bin/env python3
"""Credential-free O4 gateway manifest contracts; no Kubernetes access."""
from __future__ import annotations

import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prod_o4_push_gateway_render as gateway

CIDRS = ("10.42.0.0/24", "10.43.0.0/24")


class PushGatewayRenderTests(unittest.TestCase):
    def test_fixed_five_objects_tls_auth_and_log_post_only(self):
        doc = gateway.build_manifest(CIDRS)
        self.assertEqual((doc["apiVersion"], doc["kind"]), ("v1", "List"))
        objects = {(item["kind"], item["metadata"]["name"]): item
                   for item in doc["items"]}
        self.assertEqual(set(objects), {
            ("ConfigMap", "prod-loki-push-gateway-config"),
            ("Deployment", "prod-loki-push-gateway"),
            ("Service", "prod-loki-push-gateway"),
            ("NetworkPolicy", "prod-loki-push-gateway-restricted"),
            ("NetworkPolicy", "prod-loki-push-gateway-to-loki"),
        })
        self.assertEqual({item["metadata"]["namespace"]
                          for item in doc["items"]}, {"observability-prod"})
        self.assertFalse(any(item["kind"] == "Secret" for item in doc["items"]))
        config = objects[("ConfigMap", "prod-loki-push-gateway-config")]["data"]["nginx.conf"]
        self.assertIn("listen 8443 ssl;", config)
        self.assertIn("auth_basic_user_file /run/secrets/o4/htpasswd;", config)
        self.assertIn("location = /otlp/v1/logs {", config)
        self.assertIn("limit_except POST { deny all; }", config)
        self.assertIn('proxy_set_header Authorization "";', config)
        self.assertIn('proxy_set_header X-Scope-OrgID "";', config)
        self.assertNotIn("/loki/api/v1/query", config)
        deployment = objects[("Deployment", "prod-loki-push-gateway")]["spec"]
        pod = deployment["template"]
        self.assertEqual(deployment["replicas"], 1)
        self.assertEqual(deployment["strategy"], {"type": "Recreate"})
        self.assertEqual(pod["metadata"]["annotations"]["checksum/config"],
                         hashlib.sha256(config.encode()).hexdigest())
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
        container = pod["spec"]["containers"][0]
        self.assertEqual(container["image"], "nginx:1.30.5-alpine3.24")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual({volume["name"] for volume in pod["spec"]["volumes"]},
                         {"config", "credentials", "tmp"})
        self.assertEqual(next(volume for volume in pod["spec"]["volumes"]
                              if volume["name"] == "credentials")["secret"],
                         {"secretName": "prod-loki-push-gateway-credentials"})
        service = objects[("Service", "prod-loki-push-gateway")]["spec"]
        self.assertEqual((service["type"], service["externalTrafficPolicy"]),
                         ("NodePort", "Local"))
        self.assertEqual(service["ports"][0]["nodePort"], 32443)
        restricted = objects[("NetworkPolicy", "prod-loki-push-gateway-restricted")]["spec"]
        self.assertEqual([entry["ipBlock"]["cidr"]
                          for entry in restricted["ingress"][0]["from"]], list(CIDRS))
        self.assertEqual({rule["ports"][0]["port"] for rule in restricted["egress"]},
                         {53, 3100})
        loki_allow = objects[("NetworkPolicy", "prod-loki-push-gateway-to-loki")]["spec"]
        self.assertEqual(loki_allow["ingress"][0]["ports"],
                         [{"protocol": "TCP", "port": 3100}])

    def test_site_is_owner_only_and_cidrs_are_narrow_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / "site.json"
            site.write_text(json.dumps({"schema": 1,
                                        "staging_source_cidrs": list(CIDRS)}))
            site.chmod(0o600)
            self.assertEqual(gateway.read_site(site), CIDRS)
            site.chmod(0o644)
            with self.assertRaises(ValueError):
                gateway.read_site(site)
            site.chmod(0o600)
            for cidr in ("0.0.0.0/0", "10.0.0.0/8", "8.8.8.8/32",
                         "2001:db8::/32", "10.42.0.1/24"):
                site.write_text(json.dumps({"schema": 1,
                                            "staging_source_cidrs": [cidr]}))
                with self.subTest(cidr=cidr), self.assertRaises(ValueError) as caught:
                    gateway.read_site(site)
                self.assertNotIn(cidr, str(caught.exception))
            site.write_text('{"schema":1,"schema":1,"staging_source_cidrs":["10.42.0.0/24"]}')
            with self.assertRaises(ValueError):
                gateway.read_site(site)
            site.unlink()
            site.symlink_to(root / "absent")
            with self.assertRaises(OSError):
                gateway.read_site(site)

    def test_private_output_is_exclusive_and_stdout_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / "site.json"
            output = root / "manifest.json"
            site.write_text(json.dumps({"schema": 1,
                                        "staging_source_cidrs": list(CIDRS)}))
            site.chmod(0o600)
            stdout = StringIO()
            with (mock.patch.object(sys, "argv", ["render", "--site", str(site),
                                                   "--output", str(output)]),
                  redirect_stdout(stdout)):
                self.assertEqual(gateway.main(), 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(json.loads(output.read_text()),
                             gateway.build_manifest(CIDRS))
            with mock.patch.object(sys, "argv", ["render", "--site", str(site),
                                                     "--output", str(output)]):
                with self.assertRaises(FileExistsError):
                    gateway.main()


if __name__ == "__main__":
    unittest.main()
