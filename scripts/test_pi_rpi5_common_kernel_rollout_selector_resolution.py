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
        source_path_resolution = text.index("selector source pathを解決")
        source_artifact_validation = text.index("selector source artifactを検証")
        pre_mutation_plan = text.index("pre-mutation plan")
        mutation = text.index("fixed PXE selectorを適用")

        self.assertLess(stage_resolution, current_selector)
        self.assertLess(current_selector, selector_validation)
        self.assertLess(selector_validation, source_path_resolution)
        self.assertLess(source_path_resolution, source_artifact_validation)
        self.assertLess(source_artifact_validation, pre_mutation_plan)
        self.assertLess(pre_mutation_plan, mutation)
        self.assertIn("tasks_from: pxe_host_releases", text)

    def test_selector_source_paths_use_explicit_concatenation_not_regex_backrefs(self) -> None:
        text = PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("'/srv/gentoo/tftp-root/dates/' ~ item.value.tftp_release", text)
        self.assertIn("'/srv/gentoo/' ~ item.value.rootfs_release ~ '/nfs'", text)
        self.assertNotIn("map('regex_replace'", text)
        self.assertNotIn("/srv/gentoo/tftp-root/dates/\\\\1", text)
        self.assertNotIn("/srv/gentoo/\\\\1/nfs", text)

    def test_missing_source_paths_are_reported_before_mutation(self) -> None:
        text = PLAYBOOK.read_text(encoding="utf-8")
        missing_resolution = text.index("missing selector source pathを解決")
        source_validation = text.index("selector source artifactを検証")
        pre_mutation_plan = text.index("pre-mutation plan")
        mutation = text.index("fixed PXE selectorを適用")

        self.assertLess(missing_resolution, source_validation)
        self.assertLess(source_validation, pre_mutation_plan)
        self.assertLess(pre_mutation_plan, mutation)
        self.assertIn("homecluster_common_kernel_missing_selector_source_paths | join(',')", text)


if __name__ == "__main__":
    unittest.main()
