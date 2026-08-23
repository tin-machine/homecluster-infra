from __future__ import annotations

import unittest

from homecluster_ansible_failure import failure_diagnostics, parse_ansible_failure


class AnsibleFailureParserTests(unittest.TestCase):
    def test_parses_modern_ansible_error_and_failed_host(self) -> None:
        output = """
TASK [Pi5 common kernel target selector存在を検証] ********
task path: /repo/ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml:50
[ERROR]: Task failed: Action failed: rollout targetの現在selectorを一意に解決できません
Origin: /repo/ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml:50:7
failed: [home-router] (item=(censored due to no_log)) => {"censored":"hidden","changed":false}
"""
        failure = parse_ansible_failure(output)
        self.assertEqual(failure.task, "Pi5 common kernel target selector存在を検証")
        self.assertEqual(failure.host, "home-router")
        self.assertIn("rollout targetの現在selector", failure.message)
        self.assertIn("rpi5-common-kernel-rollout.yml:50:7", failure.origin)

    def test_parses_classic_fatal_json_message_and_rc(self) -> None:
        output = """
TASK [Run command] ***
fatal: [node-a]: FAILED! => {"changed": false, "msg": "command failed", "rc": 7}
"""
        failure = parse_ansible_failure(output)
        self.assertEqual(failure.task, "Run command")
        self.assertEqual(failure.host, "node-a")
        self.assertEqual(failure.message, "command failed")
        self.assertEqual(failure.rc, "7")

    def test_emits_machine_readable_mutation_context(self) -> None:
        diagnostics = failure_diagnostics(
            "TASK [Check selector] ***\n[ERROR]: Task failed: selector missing\nfailed: [router] => {}\n",
            stage="pre_mutation_selector_validation",
            mutation_committed=False,
            power_cycle_started=False,
            next_check_id="pxe_current_selector_resolution",
        )
        self.assertIn("ansible_failed_task=Check selector", diagnostics)
        self.assertIn("ansible_error=Task failed: selector missing", diagnostics)
        self.assertIn("runtime_mutation_committed=false", diagnostics)
        self.assertIn("power_cycle_started=false", diagnostics)
        self.assertIn("ansible_next_check_id=pxe_current_selector_resolution", diagnostics)


if __name__ == "__main__":
    unittest.main()
