"""Offline regression cases for Loki identity and old-data preservation."""
import copy
import unittest

import yaml

from check_staging_loki_upgrade import config, contract, credentials


def fixture():
    s3 = {"bucketnames": "stg-loki", "endpoint": "http://fixture:9000", "access_key_id": "fixture-access", "secret_access_key": "fixture-secret"}
    before_config = {
        "auth_enabled": False,
        "schema_config": {"configs": [{"from": "2024-01-01", "store": "boltdb-shipper", "schema": "v12", "index": {"prefix": "stg_index_"}}]},
        "common": {"storage": {"s3": s3}},
        "storage_config": {"aws": s3, "boltdb_shipper": {"shared_store": "aws", "active_index_directory": "/var/loki/index", "cache_location": "/var/loki/cache"}},
        "ruler": {"storage": {"s3": {**s3, "bucketnames": "stg-loki-ruler"}}},
        "limits_config": {"retention_period": "168h"},
    }
    workload = {"kind": "StatefulSet", "metadata": {"name": "loki-write"}, "spec": {"selector": {"matchLabels": {"component": "write"}}, "replicas": 1, "serviceName": "loki-write-headless", "volumeClaimTemplates": [{"metadata": {"name": "data"}, "spec": {"storageClassName": "nfs-client"}}]}}
    service = {"kind": "Service", "metadata": {"name": "query-scheduler-discovery"}, "spec": {"selector": {"component": "backend"}, "ports": [{"name": "grpc", "port": 9095, "targetPort": "grpc"}]}}
    old = [workload, service, {"kind": "ConfigMap", "metadata": {"name": "loki"}, "data": {"config.yaml": yaml.safe_dump(before_config)}}]
    new = copy.deepcopy(old)
    new[1]["metadata"]["name"] = "loki-query-scheduler-discovery"
    after_config = copy.deepcopy(before_config)
    after_config["storage_config"]["boltdb_shipper"].pop("shared_store")
    after_config["limits_config"]["allow_structured_metadata"] = False
    new[2]["data"]["config.yaml"] = yaml.safe_dump(after_config)
    return old, new


class LokiUpgradeContractTest(unittest.TestCase):
    def test_reviewed_compatibility_changes_pass(self):
        contract(*fixture())

    def test_identity_pvc_and_service_regressions_fail(self):
        for mutation in ("name", "pvc", "selector", "replicas", "workload", "service"):
            old, new = fixture()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                if mutation == "name":
                    new[0]["metadata"]["name"] = "replacement-write"
                elif mutation == "pvc":
                    new[0]["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] = "replacement-data"
                elif mutation == "selector":
                    new[0]["spec"]["selector"]["matchLabels"]["component"] = "different"
                elif mutation == "replicas":
                    new[0]["spec"]["replicas"] = 3
                elif mutation == "workload":
                    extra = copy.deepcopy(new[0]); extra["metadata"]["name"] = "unexpected-cache"; new.append(extra)
                else:
                    new[1]["spec"]["selector"] = {"component": "wrong"}
                contract(old, new)

    def test_storage_schema_and_binary_regressions_fail(self):
        for mutation in ("bucket", "credential", "schema", "prefix", "shared_store", "metadata", "retention", "index"):
            old, new = fixture()
            c = config(new)
            if mutation == "bucket":
                c["common"]["storage"]["s3"]["bucketnames"] = "wrong"
            elif mutation == "credential":
                c["storage_config"]["aws"]["secret_access_key"] = "wrong"
            elif mutation == "schema":
                c["schema_config"]["configs"] = []
            elif mutation == "prefix":
                c["schema_config"]["configs"][0]["index"]["prefix"] = "new_"
            elif mutation == "shared_store":
                c["storage_config"]["boltdb_shipper"]["shared_store"] = "aws"
            elif mutation == "metadata":
                c["limits_config"]["allow_structured_metadata"] = True
            elif mutation == "retention":
                c["limits_config"]["retention_period"] = "1h"
            else:
                c["storage_config"]["boltdb_shipper"]["active_index_directory"] = "/empty"
            new[2]["data"]["config.yaml"] = yaml.safe_dump(c)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contract(old, new)

    def test_missing_sensitive_bindings_fail_closed(self):
        with self.assertRaises(ValueError):
            credentials('resource "helm_release" "loki" {}')


if __name__ == "__main__":
    unittest.main()
