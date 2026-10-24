#!/usr/bin/env python3
"""Check the PXE client-vars producer against the host Tailscale boot reader."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs"
TASKS = ROLE / "tasks/pxe_runtime.yml"
TEMPLATE = ROLE / "templates/ansible-pull.client_vars.yml.j2"
BOOT_PATH = ROLE / "files/host-tailscale-boot.py"
CATALOG_PATH = ROOT / "ansible/openwrt/roles/openwrt_pxe_client_catalog/filter_plugins/openwrt_pxe_client_catalog.py"
SPEC = importlib.util.spec_from_file_location("host_tailscale_boot_contract", BOOT_PATH)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)
CATALOG_SPEC = importlib.util.spec_from_file_location("pxe_client_catalog_contract", CATALOG_PATH)
CATALOG = importlib.util.module_from_spec(CATALOG_SPEC)
CATALOG_SPEC.loader.exec_module(CATALOG)


def render(loader, value, variables):
    return Templar(loader=loader, variables=variables).template(trust_as_template(str(value)))


class HostTailscaleClientVarsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = DataLoader()
        tasks = cls.loader.load_from_file(str(TASKS))
        cls.conditions = next(
            task["ansible.builtin.assert"]["that"]
            for task in tasks
            if task.get("name") == "ansible-pull client vars 定義を検証"
        )
        cls.destination = next(
            task["ansible.builtin.template"]["dest"]
            for task in tasks
            if task.get("name") == "ansible-pull client vars を配置"
        )
        main_tasks = cls.loader.load_from_file(str(ROLE / "tasks/main.yml"))
        overlay_guard = next(
            task
            for task in main_tasks
            if task.get("name") == "Overlay クライアント定義を検証"
        )
        cls.overlay_conditions = overlay_guard["ansible.builtin.assert"]["that"]
        assert overlay_guard["tags"] == ["always"]
        assert main_tasks.index(overlay_guard) < next(
            index for index, task in enumerate(main_tasks)
            if task.get("name") == "Overlay upper ディレクトリを作成"
        )
        assert main_tasks.index(overlay_guard) < next(
            index for index, task in enumerate(main_tasks)
            if task.get("ansible.builtin.import_tasks") == "pxe_runtime.yml"
        )

    def accepted(self, overlay, role):
        item = {"overlay_id": overlay, "role": role, "vars": {"host_tailscale_enabled": True}}
        return all(
            render(self.loader, "{{ " + condition + " }}", {"item": item}) is True
            for condition in self.conditions
        )

    def overlay_accepted(self, overlay):
        return all(
            render(self.loader, "{{ " + condition + " }}", {"item": {"id": overlay}}) is True
            for condition in self.overlay_conditions
        )

    def test_rendered_client_vars_path_and_boot_reader_agree(self):
        overlay = "fixture-agent"
        role = "host_tailscale"
        self.assertTrue(self.accepted(overlay, role))
        hosts = {overlay: {
            "stage": "stg", "host_tailscale_enabled": True,
            "host_tailscale_hostname": overlay,
            "k3s_local_storage_enabled": True,
            "k3s_local_storage_device": "/dev/disk/by-label/fixture",
            "openwrt_pxe_client": {
                "enabled": True, "router": "fixture-router", "roles": ["base"],
                "overlay": {"id": overlay},
            },
        }}
        catalog = CATALOG.build_openwrt_pxe_client_catalog(hosts, {}, "fixture-router")
        item = next(row for row in catalog["ansible_pull_client_vars"] if row["role"] == role)
        self.assertEqual(item["overlay_id"], overlay)
        destination = render(self.loader, self.destination, {"item": ("/fixture-root", item)})
        relative = Path(destination).relative_to("/fixture-root")
        self.assertEqual(relative, Path("etc/ansible-pull/client_vars/fixture-agent/host_tailscale.yml"))

        rendered = render(self.loader, TEMPLATE.read_text(), {"ansible_pull_client_vars": item["vars"]})
        self.assertEqual(yaml.safe_load(rendered), item["vars"])

        config = Path("/etc/ansible-pull/client_vars") / overlay / "host_tailscale.yml"
        playbook = Path("/var/lib/ansible-pull/ansible/arm64/playbooks/host-tailscale.yml")
        def fixture_exists(path):
            return path in (config, playbook)

        with patch.dict(os.environ, {"STAGE": "stg", "OVERLAY_ID": overlay}), \
                patch.object(BOOT.Path, "is_file", fixture_exists), \
                patch.object(BOOT.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(BOOT.main(), 0)
        args = run.call_args.args[0]
        self.assertIn("@" + str(config), args)
        self.assertIn("stage=stg", args)

    def test_writer_rejects_names_that_reader_or_path_cannot_accept(self):
        for overlay, role in (("fixture.agent", "host_tailscale"), ("..", "host_tailscale"),
                              ("..", "k3s_stg_agent"), ("fixture-agent", "..")):
            with self.subTest(overlay=overlay, role=role):
                self.assertFalse(self.accepted(overlay, role))
        for overlay in (".", "..", "fixture/other"):
            with self.subTest(overlay=overlay):
                self.assertFalse(self.overlay_accepted(overlay))
        # Existing non-Tailscale role names may contain a dot without escaping the directory.
        self.assertTrue(self.overlay_accepted("fixture.agent"))
        self.assertTrue(self.accepted("fixture.agent", "k3s_stg_agent"))


if __name__ == "__main__":
    unittest.main()
