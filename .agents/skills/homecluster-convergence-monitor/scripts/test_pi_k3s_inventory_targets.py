from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("pi_k3s_inventory_targets.py")
SPEC = importlib.util.spec_from_file_location("pi_k3s_inventory_targets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class InventoryTargetTests(unittest.TestCase):
    def inventory(self) -> dict:
        return {
            "_meta": {
                "hostvars": {
                    "control-a": {
                        "ansible_host": "control.example.invalid",
                        "ansible_user": "ansible",
                    },
                    "agent-a": {
                        "ansible_host": "agent.example.invalid",
                        "ansible_user": "ops",
                    },
                }
            },
            "k3s_stg_server": {"hosts": ["control-a"]},
            "k3s_stg_agents": {"hosts": ["agent-a"]},
        }

    def test_resolves_control_agents_and_expected_count(self) -> None:
        result = module.resolve_inventory(
            self.inventory(),
            control_group="k3s_stg_server",
            agent_group="k3s_stg_agents",
            inventory_path="/example/inventory.yml",
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["control_host"], "control-a")
        self.assertEqual(result["control_ssh"], "ansible@control.example.invalid")
        self.assertEqual(result["node_hosts"], ["control-a", "agent-a"])
        self.assertEqual(result["expected_nodes"], 2)
        self.assertEqual(result["node_target_map"]["agent-a"], "ops@agent.example.invalid")

    def test_expands_child_groups(self) -> None:
        inventory = self.inventory()
        inventory["k3s_stg_agents"] = {"children": ["worker_group"]}
        inventory["worker_group"] = {"hosts": ["agent-a"]}
        result = module.resolve_inventory(
            inventory,
            control_group="k3s_stg_server",
            agent_group="k3s_stg_agents",
            inventory_path="/example/inventory.yml",
        )
        self.assertEqual(result["node_hosts"], ["control-a", "agent-a"])

    def test_rejects_multiple_control_hosts(self) -> None:
        inventory = self.inventory()
        inventory["k3s_stg_server"]["hosts"].append("agent-a")
        result = module.resolve_inventory(
            inventory,
            control_group="k3s_stg_server",
            agent_group="k3s_stg_agents",
            inventory_path="/example/inventory.yml",
        )
        self.assertEqual(result["status"], "fallback_required")
        self.assertEqual(result["reason"], "control_group_not_singleton")

    def test_rejects_duplicate_addresses(self) -> None:
        inventory = self.inventory()
        inventory["_meta"]["hostvars"]["agent-a"]["ansible_host"] = "control.example.invalid"
        result = module.resolve_inventory(
            inventory,
            control_group="k3s_stg_server",
            agent_group="k3s_stg_agents",
            inventory_path="/example/inventory.yml",
        )
        self.assertEqual(result["status"], "fallback_required")
        self.assertEqual(result["reason"], "duplicate_ansible_host")

    def test_rejects_nonstandard_port(self) -> None:
        inventory = self.inventory()
        inventory["_meta"]["hostvars"]["agent-a"]["ansible_port"] = 2222
        result = module.resolve_inventory(
            inventory,
            control_group="k3s_stg_server",
            agent_group="k3s_stg_agents",
            inventory_path="/example/inventory.yml",
        )
        self.assertEqual(result["status"], "fallback_required")
        self.assertEqual(result["reason"], "nonstandard_ansible_port_unsupported")


if __name__ == "__main__":
    unittest.main()
