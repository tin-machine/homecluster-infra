#!/usr/bin/env python3
"""Deterministic source-only fixtures. No Kubernetes API or live node access."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prod_outside_in_render as r


def fixture() -> dict:
    return {
        "api_targets": [
            {"name": "staging-api", "target": "https://api.lab.example.invalid:6443/readyz"},
        ],
        "tcp_targets": [
            {"name": "staging-node", "target": "node.lab.example.invalid:9100"},
        ],
        "egress_cidrs": ["198.51.100.0/24"],
        "api_ca_secret": None,
    }


def objects(manifest: dict) -> dict:
    return {(o["metadata"]["name"] + "-service") if o["kind"] == "Service"
            else o["metadata"]["name"]: o for o in manifest["items"]}


class ProductionOutsideInTest(unittest.TestCase):
    def test_minimal_render_is_isolated_and_bounded(self) -> None:
        m = r.build_manifest(fixture())
        self.assertEqual(m["kind"], "List")
        self.assertEqual(m["apiVersion"], "v1")
        things = objects(m)
        self.assertEqual(len(things), 9)
        self.assertEqual({x["kind"] for x in m["items"]}, {"Namespace", "ConfigMap", "Deployment", "Service", "NetworkPolicy"})
        for o in m["items"]:
            if o["kind"] != "Namespace":
                self.assertEqual(o["metadata"]["namespace"], r.NAMESPACE)
        for name in ("prod-blackbox-exporter", "prod-prometheus"):
            dep = things[name]
            self.assertEqual(dep["spec"]["replicas"], 1)
            spec = dep["spec"]["template"]["spec"]
            self.assertIs(spec["automountServiceAccountToken"], False)
            self.assertIs(spec["securityContext"]["runAsNonRoot"], True)
            sec = spec["containers"][0]["securityContext"]
            self.assertTrue(sec["readOnlyRootFilesystem"])
            self.assertFalse(sec["allowPrivilegeEscalation"])
            self.assertEqual(sec["capabilities"]["drop"], ["ALL"])
        self.assertEqual(things["prod-blackbox-exporter"]["spec"]["template"]["spec"]["containers"][0]["image"], r.BLACKBOX_IMAGE)
        self.assertEqual(things["prod-prometheus"]["spec"]["template"]["spec"]["containers"][0]["image"], r.PROMETHEUS_IMAGE)
        self.assertEqual(things["prod-blackbox-exporter-service"]["spec"]["type"], "ClusterIP")
        self.assertEqual(things["prod-prometheus-service"]["spec"]["type"], "ClusterIP")

        bb = json.loads(things["prod-blackbox-config"]["data"]["config.json"])
        self.assertEqual(bb["modules"]["http_readyz"]["http"]["valid_status_codes"], [200])
        self.assertFalse(bb["modules"]["http_readyz"]["http"]["follow_redirects"])
        self.assertNotIn("tls_config", bb["modules"]["http_readyz"]["http"])
        self.assertEqual(bb["modules"]["http_readyz"]["http"]["fail_if_body_not_matches_regexp"], [r"^ok\s*$"])
        pr = json.loads(things["prod-prometheus-config"]["data"]["prometheus.json"])
        jobs = {j["job_name"]: j for j in pr["scrape_configs"]}
        self.assertEqual(jobs["staging-api"]["static_configs"][0]["targets"], ["https://api.lab.example.invalid:6443/readyz"])
        self.assertEqual(jobs["staging-tcp"]["static_configs"][0]["targets"], ["node.lab.example.invalid:9100"])
        self.assertEqual(jobs["staging-api"]["params"]["module"], ["http_readyz"])
        self.assertEqual(jobs["staging-tcp"]["params"]["module"], ["tcp_connect"])
        self.assertNotIn("remote_write", pr)
        prom_spec = things["prod-prometheus"]["spec"]["template"]["spec"]
        self.assertEqual(prom_spec["volumes"][1]["emptyDir"]["sizeLimit"], "256Mi")
        prom_args = prom_spec["containers"][0]["args"]
        self.assertIn("--storage.tsdb.retention.time=6h", prom_args)
        self.assertIn("--storage.tsdb.retention.size=128MB", prom_args)

        bb_policy = things["prod-blackbox-restricted"]["spec"]
        self.assertEqual(bb_policy["policyTypes"], ["Ingress", "Egress"])
        allowed = bb_policy["egress"][1]["to"]
        self.assertEqual(allowed, [{"ipBlock": {"cidr": "198.51.100.0/24"}}])
        self.assertEqual({x["port"] for x in bb_policy["egress"][1]["ports"]}, {443, 6443, 9100})
        prom_policy = things["prod-prometheus-restricted"]["spec"]
        self.assertEqual(prom_policy["ingress"], [])
        self.assertEqual(prom_policy["egress"][1]["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"],
                         "prod-blackbox-exporter")
        self.assertNotIn("hostNetwork", json.dumps(m))

    def test_api_ca_from_operator_secret_only(self) -> None:
        site = fixture()
        site["api_ca_secret"] = "staging-api-ca"
        things = objects(r.build_manifest(site))
        bb = json.loads(things["prod-blackbox-config"]["data"]["config.json"])
        self.assertEqual(bb["modules"]["http_readyz"]["http"]["tls_config"]["ca_file"], "/etc/probe-ca/ca.crt")
        spec = things["prod-blackbox-exporter"]["spec"]["template"]["spec"]
        secret = next(v["secret"] for v in spec["volumes"] if v["name"] == "ca")
        self.assertEqual(secret["secretName"], "staging-api-ca")
        self.assertNotIn("secretValue", json.dumps(things))
        self.assertNotIn("insecure_skip_verify", json.dumps(things))

    def test_optional_node_targets(self) -> None:
        site = fixture()
        site["tcp_targets"] = []
        manifest = objects(r.build_manifest(site))
        prometheus_cfg = json.loads(manifest["prod-prometheus-config"]["data"]["prometheus.json"])
        self.assertNotIn("staging-tcp", [job["job_name"] for job in prometheus_cfg["scrape_configs"]])

    def test_reject_invalid_site_inputs(self) -> None:
        invalid = [
            ("unknown fields", {"unexpected": 1}),
            ("empty APIs", {"api_targets": []}),
            ("http downgrade", {"api_targets": [{"name": "api", "target": "http://api.example.invalid:6443/readyz"}]}),
            ("wrong path", {"api_targets": [{"name": "api", "target": "https://api.example.invalid:6443/healthz"}]}),
            ("redirect-capable query", {"api_targets": [{"name": "api", "target": "https://api.example.invalid:6443/readyz?x=1"}]}),
            ("embedded credentials", {"api_targets": [{"name": "api", "target": "https://user:password@api.example.invalid:6443/readyz"}]}),
            ("unexpected API port", {"api_targets": [{"name": "api", "target": "https://api.example.invalid:9443/readyz"}]}),
            ("wrong TCP port", {"tcp_targets": [{"name": "node", "target": "node.example.invalid:22"}]}),
            ("TCP with path", {"tcp_targets": [{"name": "node", "target": "node.example.invalid:9100/path"}]}),
            ("duplicate name", {"tcp_targets": [{"name": "staging-api", "target": "node.example.invalid:9100"}]}),
            ("CIDR too broad", {"egress_cidrs": ["0.0.0.0/0"]}),
            ("CIDR too broad /16", {"egress_cidrs": ["198.51.0.0/16"]}),
            ("no CIDR", {"egress_cidrs": []}),
            ("duplicate CIDR", {"egress_cidrs": ["198.51.100.0/24", "198.51.100.0/24"]}),
            ("invalid secret", {"api_ca_secret": "../oops"}),
        ]
        for name, patch_data in invalid:
            with self.subTest(case=name):
                site = fixture()
                site.update(patch_data)
                with self.assertRaises(ValueError):
                    r.build_manifest(site)

    def test_private_file_output_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            output = private / "render.json"
            r._write_private(output, r.build_manifest(fixture()))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text())["kind"], "List")
            with self.assertRaisesRegex(ValueError, "refuse overwrite"):
                r._write_private(output, r.build_manifest(fixture()))

    def test_insecure_directory_and_symlink_output_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            unsafe = Path(root) / "world"
            unsafe.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "owner-only"):
                r._write_private(unsafe / "out.json", r.build_manifest(fixture()))
            secure = Path(root) / "private"
            secure.mkdir(mode=0o700)
            target = secure / "other.json"
            target.write_text("sentinel")
            link = secure / "out.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "refuse overwrite"):
                r._write_private(link, r.build_manifest(fixture()))
            self.assertEqual(target.read_text(), "sentinel")

    def test_cli_validation_does_not_echo_targets(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            site = Path(root) / "site.json"
            site.write_text(json.dumps(fixture()))
            os.chmod(site, 0o600)
            with patch.object(sys, "argv", ["tool", "--site-config", str(site), "--validate-only"]):
                with patch("builtins.print") as printer:
                    self.assertEqual(r.main(), 0)
                    content = repr(printer.call_args_list)
                    self.assertNotIn("api.lab.example.invalid", content)
                    self.assertIn("outside-in manifest validated", content)


if __name__ == "__main__":
    unittest.main()
