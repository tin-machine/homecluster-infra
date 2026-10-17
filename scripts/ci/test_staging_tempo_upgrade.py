"""Offline regressions for the Tempo upgrade test; no cluster or network access."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from check_staging_tempo_upgrade import assert_trace, check_values, contract, release


def fixture(candidate=False):
    settings = {"storage": {"trace": {"backend": "local", "local": {"path": "/var/tempo/traces"},
                                    "wal": {"path": "/var/tempo/wal"}}},
                "compactor": {"compaction": {"block_retention": "24h"}}, "overrides": {"defaults": {}},
                "multitenancy_enabled": False, "distributor": {"receivers": {"otlp": {"protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"}, "http": {"endpoint": "0.0.0.0:4318"}}}}}}
    if candidate:
        settings["storage"]["trace"]["block"] = {"version": "vParquet4"}
    claim = {"metadata": {"name": "storage"}, "spec": {"accessModes": ["ReadWriteOnce"],
             "storageClassName": "nfs-client", "resources": {"requests": {"storage": "20Gi"}}}}
    if candidate:
        claim.update(apiVersion="v1", kind="PersistentVolumeClaim")
    container = {"name": "tempo", "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "512Mi"}},
                 "args": ["-mem-ballast-size-mbs=0"], "env": [{"name": "GOMEMLIMIT", "value": "400MiB"}],
                 "volumeMounts": [{"name": "storage", "mountPath": "/var/tempo"}]}
    return [{"kind": "StatefulSet", "metadata": {"name": "stg-tempo"}, "spec": {
        "replicas": 1, "selector": {"matchLabels": {"app": "tempo"}}, "serviceName": "stg-tempo-headless",
        "updateStrategy": {"type": "RollingUpdate"}, "volumeClaimTemplates": [claim],
        "template": {"spec": {"containers": [container]}}}},
        {"kind": "ConfigMap", "data": {"tempo.yaml": yaml.safe_dump(settings)}},
        {"kind": "Service", "metadata": {"name": "stg-tempo"}, "spec": {"ports": [{"port": 4318}]}}]


class TempoUpgradeTest(unittest.TestCase):
    def test_typed_claim_is_allowed_only_when_other_fields_match(self):
        before, after = fixture(), fixture(True)
        preserved = copy.deepcopy(after)
        contract(before, after)
        self.assertEqual(preserved, after)

    def test_reject_destructive_manifest_changes(self):
        for change in ("claim", "size", "class", "selector", "retention", "replicas", "mount", "service", "memory", "ballast", "gomemlimit"):
            with self.subTest(change=change):
                before, after = fixture(), fixture(True)
                spec = after[0]["spec"]
                container = spec["template"]["spec"]["containers"][0]
                if change == "claim":
                    spec["volumeClaimTemplates"][0]["metadata"]["name"] = "new-storage"
                elif change == "size":
                    spec["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] = "30Gi"
                elif change == "class":
                    spec["volumeClaimTemplates"][0]["spec"]["storageClassName"] = "different"
                elif change == "selector":
                    spec["selector"] = {"matchLabels": {"app": "different"}}
                elif change == "retention":
                    spec["persistentVolumeClaimRetentionPolicy"] = {"whenDeleted": "Delete", "whenScaled": "Delete"}
                elif change == "replicas":
                    spec["replicas"] = 2
                elif change == "mount":
                    container["volumeMounts"][0]["mountPath"] = "/different"
                elif change == "service":
                    after[2]["spec"]["ports"][0]["port"] = 14318
                elif change == "memory":
                    container["resources"] = {}
                elif change == "ballast":
                    container["args"] = []
                else:
                    container["env"] = []
                with self.assertRaises(ValueError):
                    contract(before, after)

    def test_reject_incompatible_tempo_config(self):
        for change in ("wal", "format", "receiver", "tls", "retention", "tenant"):
            with self.subTest(change=change):
                before, after = fixture(), fixture(True)
                config = yaml.safe_load(after[1]["data"]["tempo.yaml"])
                if change == "wal":
                    config["storage"]["trace"]["wal"]["path"] = "/empty"
                elif change == "format":
                    config["storage"]["trace"]["block"]["version"] = "vParquet5-preview1"
                elif change == "receiver":
                    config["distributor"]["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = "127.0.0.1:4318"
                elif change == "tls":
                    config["distributor"]["receivers"]["otlp"]["protocols"]["http"]["tls"] = {"enabled": False}
                elif change == "retention":
                    config["compactor"]["compaction"]["block_retention"] = "1h"
                else:
                    config["multitenancy_enabled"] = True
                after[1]["data"]["tempo.yaml"] = yaml.safe_dump(config)
                with self.assertRaises(ValueError):
                    contract(before, after)

    def test_ignored_helm_keys_are_rejected(self):
        for values in ({"resources": {}, "tempo": {}}, {"tempo": {"distributor": {}}}):
            with self.assertRaises(ValueError):
                check_values(values)
        check_values(yaml.safe_load((Path(__file__).resolve().parents[2] /
                                    "clusters/homelab/apps/staging/values-tempo.yaml").read_text()))

    def test_release_is_read_from_the_tempo_resource_only(self):
        self.assertEqual(release('resource "helm_release" "tempo" {\n repository = "https://example.invalid"\n version = "1.24.4"\n}'),
                         ("https://example.invalid", "1.24.4"))
        with self.assertRaises(ValueError):
            release('resource "helm_release" "grafana" {\n}')

    def test_trace_contents_and_blocks_endpoint(self):
        span = {"traceId": "11223344556677889900aabbccddeeff", "spanId": "1122334455667788",
                "name": "test", "startTimeUnixNano": "1234", "endTimeUnixNano": "5678", "attributes": []}
        with patch("check_staging_tempo_upgrade.http", return_value={"batches": [{"spans": [span]}]}) as mock:
            assert_trace(span, blocks=True)
            self.assertIn("/querier/api/traces/", mock.call_args.args[1])
            self.assertTrue(mock.call_args.args[1].endswith("?mode=blocks"))
        for mutated in ({}, {**span, "endTimeUnixNano": "9999"}, {**span, "attributes": [{"key": "changed"}]}):
            with patch("check_staging_tempo_upgrade.http", return_value={"spans": [mutated]}):
                with self.assertRaises(ValueError):
                    assert_trace(span)


if __name__ == "__main__":
    unittest.main()
