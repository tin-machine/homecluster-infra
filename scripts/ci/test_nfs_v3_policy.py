#!/usr/bin/env python3
"""Test NFS boot policy propagation and the pinned chart's real consumers."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/arm64/roles/common/nfs_client_policy"
SPEC = importlib.util.spec_from_file_location("catalog_nfs", ROOT / "ansible/openwrt/roles/openwrt_pxe_client_catalog/filter_plugins/openwrt_pxe_client_catalog.py")
CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CATALOG)


class NFSPolicyTest(unittest.TestCase):
    def test_boot_paths_preserve_only_explicit_server_policy(self):
        for role in ("base", "k3s_stg_server", "k3s_stg_agent", "k3s_prd_server"):
            with self.subTest(role=role):
                args = ("fixture", {"ansible_host": "192.0.2.4", "nfs_client_v3_servers": ["192.0.2.10"]}, {}, [role], "fixture")
                _, entries = CATALOG._generated_role_client_vars(*args)
                self.assertEqual(next(e["vars"] for e in entries if e["role"] == role)["nfs_client_v3_servers"], ["192.0.2.10"])
                _, absent = CATALOG._generated_role_client_vars("fixture", {"ansible_host": "192.0.2.4"}, {}, [role], "fixture")
                self.assertTrue(all("nfs_client_v3_servers" not in e["vars"] for e in absent))

    def test_rejects_configuration_injection(self):
        for value in ("192.0.2.10", [None], ["bad\nNfsvers=4"], ['bad" ]'], {"bad": 3}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CATALOG._generated_role_client_vars("fixture", {"nfs_client_v3_servers": value}, {}, ["base"], "fixture")

    def test_template_has_exact_server_scope_and_remote_locking(self):
        env = Environment(loader=FileSystemLoader(ROLE / "templates"), undefined=StrictUndefined)
        text = env.get_template("server-v3.conf.j2").render(nfs_client_v3_servers=["192.0.2.10", "192.0.2.10"])
        self.assertEqual(text.count('[ Server "192.0.2.10" ]'), 1)
        self.assertIn("Nfsvers=3", text)
        self.assertNotIn("NFSMount_Global_Options", text)
        self.assertNotIn("nolock", text.lower())
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        self.assertTrue(all("notify" not in task for task in tasks))
        self.assertFalse(any("ansible.builtin.command" in task or "ansible.builtin.service" in task for task in tasks))

    def test_pinned_chart_renders_version_for_root_and_dynamic_volumes(self):
        # This guards against a values key accepted by Helm but silently unused.
        source = (ROOT / "terraform/env/common-addons/main.tf").read_text()
        block = source.split('resource "helm_release" "nfs_provisioner"', 1)[1]
        self.assertIn('version    = "4.0.18"', block)
        nfs = block.split("    nfs = {", 1)[1].split("    storageClass = {", 1)[0]
        self.assertIn("mountOptions = [", nfs)
        options = ["vers=3", "rsize=1048576", "wsize=1048576", "hard", "timeo=600", "retrans=2"]
        self.assertTrue(all('"' + option + '"' in nfs for option in options))
        values = {"nfs": {"server": "192.0.2.10", "path": "/fixture", "mountOptions": options}, "storageClass": {"name": "fixture-nfs", "reclaimPolicy": "Retain", "onDelete": "retain"}}
        result = subprocess.run(["helm", "template", "fixture", "nfs-subdir-external-provisioner", "--repo", "https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner", "--version", "4.0.18", "--values", "-"], input=yaml.safe_dump(values), text=True, capture_output=True, timeout=120, check=True)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        for kind in ("StorageClass", "PersistentVolume"):
            obj = next(d for d in docs if d["kind"] == kind)
            self.assertEqual((obj if kind == "StorageClass" else obj["spec"])["mountOptions"], options)
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        self.assertIn("persistentVolumeClaim", deployment["spec"]["template"]["spec"]["volumes"][0])
        self.assertEqual(next(d for d in docs if d["kind"] == "PersistentVolume")["spec"]["persistentVolumeReclaimPolicy"], "Retain")


if __name__ == "__main__":
    unittest.main()
