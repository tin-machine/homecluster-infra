"""Post-accept cleanup may run only with exact build and rollback identities."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parent / "pi-rpi5-common-kernel-rollout"
loader = importlib.machinery.SourceFileLoader("postaccept_cleanup_test", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


class PostacceptCleanupTests(unittest.TestCase):
    def test_exact_rollback_selectors_are_passed_after_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            playbook = root / "ansible/arm64/playbooks/rpi5-common-kernel-postaccept-cleanup.yml"
            playbook.parent.mkdir(parents=True)
            playbook.write_text("---\n")
            previous = {
                "agent-a": {"tftp_release": "20261020-rpi5", "rootfs_release": "20261020-rpi5"},
                "agent-b": {"tftp_release": "20261021-rpi5", "rootfs_release": "20261020-rpi5"},
            }
            with patch.object(rollout, "run", return_value=subprocess.CompletedProcess(
                ["ansible-playbook"], 0, "", "",
            )) as run:
                result = rollout.postaccept_cleanup(
                    root, root / "inventory.yml", ["agent-a", "agent-b"],
                    {"kernel_artifact_build_release": "20261029-rpi5"},
                    {"previous_selector_by_node": previous},
                )
            self.assertEqual(result, ["postaccept_cleanup=pass"])
            command = run.call_args.args[0]
            values = json.loads(command[command.index("--extra-vars") + 1])
            self.assertEqual(values["homecluster_cleanup_accepted"], "20261029-rpi5-homecluster")
            self.assertEqual(values["homecluster_cleanup_previous_selectors"], [
                "20261020-rpi5", "20261021-rpi5",
            ])

    def test_missing_rollback_identity_does_not_launch_cleanup(self):
        with patch.object(rollout, "run") as run:
            result = rollout.postaccept_cleanup(
                Path("/unused"), Path("/unused/inventory.yml"), ["agent-b"],
                {"kernel_artifact_build_release": "20261029-rpi5"},
                {"previous_selector_by_node": {}},
            )
        self.assertEqual(result, ["postaccept_cleanup=blocked_previous_selectors_unknown"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
