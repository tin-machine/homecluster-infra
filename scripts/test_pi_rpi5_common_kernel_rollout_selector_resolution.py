from __future__ import annotations

import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAYBOOK = HERE.parent / "ansible/openwrt/playbooks/rpi5-common-kernel-rollout.yml"


class RolloutSelectorResolutionContractTests(unittest.TestCase):
    def test_stage_release_is_resolved_before_current_selector_validation(self) -> None:
        text = PLAYBOOK.read_text(encoding="utf-8")
        stage_resolution = text.index("current PXE releaseをstage dateから解決")
        current_selector = text.index("target current selectorを解決")
        selector_validation = text.index("target selector存在を検証")
        pre_mutation_plan = text.index("pre-mutation plan")
        mutation = text.index("fixed PXE selectorを適用")

        self.assertLess(stage_resolution, current_selector)
        self.assertLess(current_selector, selector_validation)
        self.assertLess(selector_validation, pre_mutation_plan)
        self.assertLess(pre_mutation_plan, mutation)
        self.assertIn("tasks_from: pxe_host_releases", text)


if __name__ == "__main__":
    unittest.main()
