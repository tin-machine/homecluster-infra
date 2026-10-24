#!/usr/bin/env python3
"""Mutation fixtures for the staged k3s converge source contract."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).with_name("check-k3s-converge-contract.py")
SPEC = importlib.util.spec_from_file_location("k3s_converge_contract", SOURCE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class K3sConvergeContractTest(unittest.TestCase):
    def test_checked_in_sources_pass(self):
        self.assertEqual(contract.main(), 0)

    def test_lifecycle_helper_requires_explicit_start_mode(self):
        adr_path = "docs/architecture-decision-record/0014-k3s-converge-wrapper-contract.md"
        adr = contract.read_rel(adr_path)
        defaults = contract.read_rel("ansible/arm64/roles/k3s_converge_check/defaults/main.yml")
        helper = contract.read_rel("ansible/arm64/roles/k3s_converge_check/files/k3s-converge")
        failures = []

        self.assertTrue(contract.check_adr_and_helper(
            adr_path, adr, defaults, helper.replace("--start", "--run"), failures,
        ))
        self.assertIn(
            "k3s-converge lifecycle commands require an explicit start/restart mode",
            failures,
        )

    def test_site_wiring_rejects_agent_role_order_change(self):
        site = contract.read_rel("ansible/arm64/site.yml")
        converge_tasks = contract.read_rel("ansible/arm64/roles/k3s_converge_check/tasks/main.yml")
        agent_start = site.index("- name: k3s staging agents")
        agent_end = site.index("\n- name:", agent_start + 1)
        agent_play = site[agent_start:agent_end]
        network_line = "    - role: k3s_networking"
        converge_line = "    - role: k3s_converge_check"
        self.assertIn(network_line, agent_play)
        self.assertIn(converge_line, agent_play)
        broken = agent_play.replace(network_line, "    - role: TEMP_ROLE", 1)
        broken = broken.replace(converge_line, network_line, 1)
        broken = broken.replace("    - role: TEMP_ROLE", converge_line, 1)
        failures = []

        contract.check_site_wiring(
            site[:agent_start] + broken + site[agent_end:], converge_tasks, True, failures,
        )
        self.assertIn(
            "k3s agent wiring order must be xanmanning.k3s -> "
            "k3s_agent_install_config -> k3s_networking -> k3s_converge_check",
            failures,
        )

    def test_server_template_rejects_jinja_block_on_exec_start(self):
        tasks_path = "ansible/arm64/roles/k3s_server_install_config/tasks/main.yml"
        defaults_path = "ansible/arm64/roles/k3s_server_install_config/defaults/main.yml"
        template_path = "ansible/arm64/roles/k3s_server_install_config/templates/k3s-server.service.j2"
        template = contract.read_rel(template_path)
        failures = []

        contract.check_server_scaffold(
            tasks_path, contract.read_rel(tasks_path),
            defaults_path, contract.read_rel(defaults_path),
            template_path, template.replace("ExecStart=", "ExecStart={% if true %}", 1),
            failures,
        )
        self.assertTrue(any("must not put Jinja block tags on ExecStart" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
