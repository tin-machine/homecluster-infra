"""Source-only fixtures for the boot-scoped post-pull checkout receipt."""
from __future__ import annotations

import re
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-ansible-pull-wrapper.sh.j2"
BOOT = "123e4567-e89b-12d3-a456-426614174000"
HEAD = "a" * 40


def shell_function(code: str, name: str) -> str:
    expression = rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n"
    found = re.search(expression, code)
    if found is None:
        raise AssertionError(f"missing function {name}")
    return found.group(0)


class PostPullReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        code = WRAPPER.read_text(encoding="utf-8")
        cls.source = "\n".join(
            shell_function(code, name)
            for name in ("state_file_for_role", "read_boot_id", "record_post_pull_identity")
        )
        cls.wrapper = code

    def simulate(
        self, directory: Path, *, rc="0", step="complete", stage="stg",
        branch="stg", role="k3s_stg_agent", head=HEAD, boot=BOOT,
    ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        state = directory / "state"
        dest = directory / "checkout"
        state.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir(parents=True, exist_ok=True)
        marker = state / (re.sub(r"[^A-Za-z0-9_.@-]", "_", role) + ".post-pull.env")
        values = {
            "STATE_DIR": str(state), "DEST": str(dest), "ROLE": role,
            "STAGE": stage, "BRANCH": branch, "CURRENT_STEP": step,
        }
        bindings = "\n".join(f"{name}={shlex.quote(value)}" for name, value in values.items())
        script = (
            "set -eu\n"
            + bindings + "\n"
            + self.source
            + "\n"
            + "read_boot_id() { printf '%s\\n' " + shlex.quote(boot) + "; }\n"
            + "git() { printf '%s\\n' " + shlex.quote(head) + "; }\n"
            + "now_epoch() { printf '%s\\n' 123456; }\n"
            + "record_post_pull_identity " + shlex.quote(rc) + "\n"
        )
        completed = subprocess.run(
            ["sh", "-c", script], text=True, capture_output=True, check=False, timeout=5,
        )
        return marker, completed

    def test_success_writes_exact_sanitized_receipt_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            self.assertEqual(
                marker.read_text().splitlines(),
                ["SCHEMA=1", "ROLE=k3s_stg_agent", "STAGE=stg", "BRANCH=stg",
                 "BOOT_ID=" + BOOT, "CHECKOUT_HEAD_AFTER_SUCCESS=" + HEAD,
                 "RECORDED_AT_EPOCH=123456"],
            )
            self.assertFalse(list(marker.parent.glob("*.tmp.*")))

    def test_failure_invalidates_old_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker, good = self.simulate(root)
            self.assertEqual(good.returncode, 0)
            self.assertTrue(marker.exists())
            marker, failed = self.simulate(root, rc="1", step="ansible_pull")
            self.assertEqual(failed.returncode, 0)
            self.assertFalse(marker.exists())

    def test_nonterminal_step_cannot_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp), step="ansible_pull")
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_prod_never_publishes_staging_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp), stage="prod", branch="main")
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_wrong_branch_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, _ = self.simulate(Path(temp), stage="stg", branch="main")
            self.assertFalse(marker.exists())

    def test_invalid_git_head_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp), head="branch-stg")
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_bad_boot_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp), boot="not-a-boot")
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_role_with_shell_metacharacters_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker, result = self.simulate(Path(temp), role="bad;role")
            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_keeps_existing_ansible_invocation_and_status_semantics(self) -> None:
        text = self.wrapper
        self.assertIn('record_post_pull_identity "${rc}" || true', text)
        self.assertIn('log_state "${rc}" "${elapsed}"', text)
        self.assertIn('exit "${rc}"', text)
        self.assertEqual(text.count("/usr/bin/ansible-pull -U"), 2)
        self.assertIn('-C "${BRANCH}" -d "${DEST}"', text)
        self.assertIn("post-pull.env", text)
        self.assertNotIn("EXECUTED_REVISION=", text)
        checked = subprocess.run(
            ["sh", "-n", str(WRAPPER)], capture_output=True, text=True,
            check=False, timeout=5,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
