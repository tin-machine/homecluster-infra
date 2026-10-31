"""Production host monitoring contract, using public-safe fixture identities."""
import copy
import json
import re
import unittest

from prod_grafana_terraform_input import terraform_manifest
from prod_node_host_monitoring import dashboard, exporter
from test_prod_outside_in_render import fixture


def site():
    value = fixture()
    value["metrics_targets"] = [
        {"name": f"worker-{n}", "target": f"192.0.2.{n}:9100"} for n in range(1, 5)]
    value["prod_metrics_target"] = {
        "name": "example-prod", "target": "example-prod.lab.example.invalid:9100",
        "address": "198.51.100.5",
    }
    return value


def objects(manifest):
    return {item["kind"] + "/" + item["metadata"]["name"]: item for item in manifest["items"]}


class ProductionHostTests(unittest.TestCase):
    def test_opt_in_preserves_other_objects_and_dashboards(self):
        input_site = site()
        original = copy.deepcopy(input_site)
        old = objects(terraform_manifest(prometheus_site=input_site,
                                        desktop_host_address="192.0.2.23", include_attention=True))
        new = objects(terraform_manifest(prometheus_site=input_site,
                                        desktop_host_address="192.0.2.23", include_attention=True,
                                        include_prod_host=True))
        self.assertEqual(input_site, original)
        self.assertEqual(set(new) - set(old), {"DaemonSet/prod-node-exporter"})
        self.assertNotIn("DaemonSet/prod-node-exporter", old)
        for name in old:
            if name not in {"ConfigMap/prod-grafana-provisioning", "Deployment/prod-grafana"}:
                self.assertEqual(old[name], new[name], name)
        before = old["ConfigMap/prod-grafana-provisioning"]["data"]
        after = new["ConfigMap/prod-grafana-provisioning"]["data"]
        for key in before:
            if key != "dashboard-provider.yaml":
                self.assertEqual(before[key], after[key], key)
        providers = json.loads(after["dashboard-provider.yaml"])["providers"]
        self.assertEqual(providers[:-1], json.loads(before["dashboard-provider.yaml"])["providers"])
        self.assertEqual(providers[-1]["folderUid"], "prod-hosts")
        self.assertEqual(providers[-1]["folder"], "Production")
        self.assertIn("prod-hosts.json", after)
        old_template = old["Deployment/prod-grafana"]["spec"]["template"]
        new_template = new["Deployment/prod-grafana"]["spec"]["template"]
        self.assertNotEqual(old_template["metadata"]["annotations"]["checksum/provisioning"],
                            new_template["metadata"]["annotations"]["checksum/provisioning"])
        self.assertEqual(old_template["spec"]["containers"][0]["env"],
                         new_template["spec"]["containers"][0]["env"])

    def test_exporter_reads_host_and_binds_host_address(self):
        daemon = exporter("example-prod")
        self.assertEqual(daemon["metadata"]["namespace"], "observability-prod")
        pod = daemon["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {
            "kubernetes.io/hostname": "example-prod", "kubernetes.io/os": "linux"})
        self.assertTrue(pod["hostNetwork"])
        self.assertTrue(pod["hostPID"])
        self.assertFalse(pod["automountServiceAccountToken"])
        container = pod["containers"][0]
        self.assertEqual(container["env"][0]["valueFrom"]["fieldRef"]["fieldPath"], "status.hostIP")
        for arg in ["--web.listen-address=$(HOST_IP):9100", "--path.rootfs=/host",
                    "--path.procfs=/host/proc", "--path.sysfs=/host/sys"]:
            self.assertIn(arg, container["args"])
        self.assertEqual(pod["volumes"], [{"name": "host-root", "hostPath": {"path": "/", "type": "Directory"}}])
        self.assertTrue(container["volumeMounts"][0]["readOnly"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertNotIn("privileged", container["securityContext"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        # PXE root can be overlay: the default exclusion must not hide it.
        fs_filter = next(arg.split("=", 1)[1] for arg in container["args"]
                         if arg.startswith("--collector.filesystem.fs-types-exclude="))
        self.assertIsNone(re.fullmatch(fs_filter, "overlay"))
        self.assertIsNotNone(re.fullmatch(fs_filter, "proc"))

    def test_name_based_scrape_and_scoped_egress(self):
        result = objects(terraform_manifest(prometheus_site=site(), include_prod_host=True))
        config = json.loads(result["ConfigMap/prod-prometheus-config"]["data"]["prometheus.json"])
        job = next(j for j in config["scrape_configs"] if j["job_name"] == "prod-node-exporter")
        self.assertEqual(job["static_configs"], [{
            "targets": ["example-prod.lab.example.invalid:9100"],
            "labels": {"node_name": "example-prod"}}])
        self.assertIn({"to": [{"ipBlock": {"cidr": "198.51.100.5/32"}}],
                       "ports": [{"protocol": "TCP", "port": 9100}]},
                      result["NetworkPolicy/prod-prometheus-restricted"]["spec"]["egress"])
        for metric in ("node_cpu_seconds_total", "node_memory_MemAvailable_bytes",
                       "node_filesystem_avail_bytes", "node_hwmon_temp_celsius",
                       "node_disk_written_bytes_total", "node_network_receive_bytes_total",
                       "node_boot_time_seconds"):
            self.assertIsNotNone(re.fullmatch(job["metric_relabel_configs"][0]["regex"], metric))
        value = dashboard()
        self.assertEqual(value["uid"], "prod-hosts")
        self.assertEqual(value["title"], "Production hosts")
        for panel in value["panels"]:
            self.assertEqual(panel["datasource"]["uid"], "prod-prometheus")
            self.assertEqual(panel["fieldConfig"]["defaults"]["noValue"], "未観測")
            self.assertIn('job="prod-node-exporter"', panel["targets"][0]["expr"])
        self.assertNotIn("localhost", json.dumps(job))

    def test_explicit_target_required_and_bad_node_names_rejected(self):
        for value in (None, fixture()):
            with self.subTest(site=value), self.assertRaises(ValueError):
                terraform_manifest(prometheus_site=value, include_prod_host=True)
        for value in (None, "", "../escape", "host.invalid", "-host", "host_1", "x" * 64):
            with self.subTest(node=value), self.assertRaises(ValueError):
                exporter(value)


if __name__ == "__main__":
    unittest.main()
