"""Offline negative fixtures for the actual-HCL / BGP upgrade check."""
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from check_staging_metallb_upgrade import VIP, assert_route, contract, evaluate, resource


def docs():
    result = []
    for kind, component in (("Deployment", "controller"), ("DaemonSet", "speaker")):
        container = {"name": component, "env": [{"name": "METALLB_BGP_TYPE", "value": "frr"}],
                     "args": ["--port=9120"], "readinessProbe": {"httpGet": {"port": 17472}}}
        containers = [container] + ([{"name": "frr"}] if component == "speaker" else [])
        result.append({"kind": kind, "metadata": {"name": f"metallb-{component}"}, "spec": {
            "selector": {"matchLabels": {"component": component}}, "template": {"spec": {
                "containers": containers, "hostNetwork": component == "speaker", "nodeSelector": {"kubernetes.io/os": "linux"},
                "tolerations": [], "serviceAccountName": component}}}})
    schema = {"properties": {"spec": {"properties": {k: {"format": "int64"} for k in ("myASN", "peerASN", "localASN")}}}}
    result.append({"kind": "CustomResourceDefinition", "metadata": {"name": "bgppeers.metallb.io"},
                   "spec": {"versions": [{"name": "v1beta2", "schema": {"openAPIV3Schema": schema}}]}})
    result.append({"kind": "Service", "metadata": {"name": "webhook"}, "spec": {"ports": [{"port": 443}]}})
    return result


class MetalLBTest(unittest.TestCase):
    def test_multiline_hcl_wrapper(self):
        # The live CI also evaluates this with the pinned Terraform binary.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "expression"
            with patch("check_staging_metallb_upgrade.run", return_value=json.dumps('{"ok": true}')):
                self.assertEqual(evaluate("{\n ok = true\n}", {}, directory), {"ok": True})
            self.assertIn("locals {\n  result = {\n", (directory / "main.tf").read_text())

    def test_contract_does_not_mutate(self):
        old, new = docs(), docs()
        saved = copy.deepcopy(new)
        contract(old, new)
        self.assertEqual(new, saved)

    def test_backend_and_routing_changes_rejected(self):
        for change in ("backend", "duplicate", "frr-missing", "frrk8s", "selector", "name", "host", "selector-node", "toleration", "serviceaccount", "metrics", "health", "service", "crd", "asn"):
            with self.subTest(change=change):
                old, new = docs(), docs()
                pod = new[1]["spec"]["template"]["spec"]
                controller = new[0]["spec"]["template"]["spec"]["containers"][0]
                if change == "backend":
                    pod["containers"][0]["env"][0]["value"] = "frr-k8s"
                elif change == "duplicate":
                    pod["containers"][0]["env"].append({"name": "METALLB_BGP_TYPE", "value": "frr-k8s"})
                elif change == "frr-missing":
                    pod["containers"].pop()
                elif change == "frrk8s":
                    new.append({"kind": "ConfigMap", "metadata": {"name": "metallb-frr-k8s"}})
                elif change == "selector":
                    new[1]["spec"]["selector"] = {}
                elif change == "name":
                    new[1]["metadata"]["name"] = "new"
                elif change == "host":
                    pod["hostNetwork"] = False
                elif change == "selector-node":
                    pod["nodeSelector"] = {}
                elif change == "toleration":
                    pod["tolerations"] = [{"operator": "Exists"}]
                elif change == "serviceaccount":
                    pod["serviceAccountName"] = "new"
                elif change == "metrics":
                    controller["args"] = ["--port=7472"]
                elif change == "health":
                    controller["readinessProbe"]["httpGet"]["port"] = 7472
                elif change == "service":
                    new[3]["spec"]["ports"][0]["port"] = 9443
                elif change == "crd":
                    old.append({"kind": "CustomResourceDefinition", "metadata": {"name": "old"}})
                else:
                    props = new[2]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
                    props["myASN"]["format"] = "int32"
                with self.assertRaises(ValueError):
                    contract(old, new)

    def test_only_valid_best_route_to_the_node_passes(self):
        node = "192.0.2.2"
        good = {"routes": {f"{VIP}/32": [{"valid": True, "bestpath": True, "nexthops": [{"ip": node}]}]}}
        assert_route(good, node)
        for mutation in ("missing", "invalid", "not-best", "nexthop", "prefix"):
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(good)
                route = candidate["routes"][f"{VIP}/32"][0]
                if mutation == "missing":
                    candidate = {}
                elif mutation == "invalid":
                    route["valid"] = False
                elif mutation == "not-best":
                    route["bestpath"] = False
                elif mutation == "nexthop":
                    route["nexthops"] = [{"ip": "192.0.2.3"}]
                else:
                    candidate["routes"][f"{VIP}/24"] = candidate["routes"].pop(f"{VIP}/32")
                with self.assertRaises(ValueError):
                    assert_route(candidate, node)

    def test_resource_extraction_is_scoped_and_fail_closed(self):
        source = 'resource "helm_release" "other" {\n version = "wrong"\n}\nresource "helm_release" "metallb" {\n values = [yamlencode({\n    a = true\n  })]\n}\n'
        body = resource(source, "helm_release", "metallb")
        self.assertNotIn("wrong", body)
        self.assertIn("yamlencode", body)
        for invalid in (source.replace('"metallb"', '"different"'), source + source):
            with self.assertRaises(ValueError):
                resource(invalid, "helm_release", "metallb")


if __name__ == "__main__":
    unittest.main()
