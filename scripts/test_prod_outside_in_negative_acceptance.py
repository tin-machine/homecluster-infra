#!/usr/bin/env python3
"""Contract checks for disposable O1 negative-probe acceptance resources."""

from __future__ import annotations

import json
import unittest

from prod_outside_in_negative_acceptance import (
    BLACKBOX_LABELS,
    NAME,
    POD_LABELS,
    TEST_TARGET,
    manifest,
)
from prod_outside_in_render import NAMESPACE, PROMETHEUS_IMAGE


class NegativeAcceptanceTests(unittest.TestCase):
    def test_fixed_scope_and_ownership(self):
        items = manifest()["items"]
        self.assertEqual(
            {(item["kind"], item["metadata"]["name"]) for item in items},
            {("ConfigMap", NAME), ("NetworkPolicy", NAME), ("Pod", NAME)},
        )
        self.assertTrue(all(item["metadata"]["namespace"] == NAMESPACE for item in items))
        pod = next(item for item in items if item["kind"] == "Pod")
        self.assertEqual(pod["metadata"]["labels"], POD_LABELS)
        self.assertNotEqual(POD_LABELS["app.kubernetes.io/name"], "prod-prometheus")
        self.assertNotEqual(POD_LABELS["app.kubernetes.io/part-of"], "prod-outside-in")
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertEqual(pod["spec"]["containers"][0]["image"], PROMETHEUS_IMAGE)
        self.assertEqual(pod["spec"]["volumes"][1]["emptyDir"]["sizeLimit"], "256Mi")

    def test_negative_probe_uses_existing_blackbox_only(self):
        items = manifest()["items"]
        config = next(item for item in items if item["kind"] == "ConfigMap")
        job = json.loads(config["data"]["prometheus.json"])["scrape_configs"][0]
        self.assertEqual(job["static_configs"][0]["targets"], [TEST_TARGET])
        self.assertEqual(job["params"], {"module": ["http_readyz"]})
        self.assertEqual(job["relabel_configs"][-1]["replacement"], "prod-blackbox-exporter:9115")
        policy = next(item for item in items if item["kind"] == "NetworkPolicy")
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], BLACKBOX_LABELS)
        self.assertEqual(policy["spec"]["policyTypes"], ["Ingress"])
        self.assertEqual(policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"], POD_LABELS)
        self.assertEqual(policy["spec"]["ingress"][0]["ports"], [{"protocol": "TCP", "port": 9115}])


if __name__ == "__main__":
    unittest.main()
