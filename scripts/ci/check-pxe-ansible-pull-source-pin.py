#!/usr/bin/env python3
"""Synthetic boot-wrapper regression harness: local Git and mocked external tools."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-ansible-pull-wrapper.sh.j2"
)


class WrapperFixture(unittest.TestCase):
    def run_wrapper(self, *, move_branch):
        with tempfile.TemporaryDirectory(
            prefix="homecluster-boot-pin-fixture-"
        ) as name:
            root = Path(name)
            repo, bins, state = root / "origin", root / "bin", root / "run"
            repo.mkdir()
            bins.mkdir()
            state.mkdir()
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("GIT_", "ANSIBLE_", "SOPS_", "SSH_"))
            }
            env.update(
                HOME=str(root / "home"),
                GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL="/dev/null",
                GIT_TERMINAL_PROMPT="0",
                GIT_ALLOW_PROTOCOL="file",
                PATH=str(bins) + ":/usr/bin:/bin",
                ORIGIN=str(repo),
                TRACE=str(root / "trace.jsonl"),
                INITIAL_TRACE=str(root / "initial-head"),
            )

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(repo), *args],
                    env=env,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()

            git("init", "-b", "stg")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            play = repo / "ansible/arm64"
            play.mkdir(parents=True)
            for path, content in [
                ("site.yml", "---\n- hosts: localhost\n  tasks: []\n"),
                ("requirements.yml", "---\nroles: []\ncollections: []\n"),
                ("ansible.cfg", "[defaults]\n"),
            ]:
                (play / path).write_text(content)
            (repo / "revision.txt").write_text("A\n")
            git("add", ".")
            git("commit", "-m", "fixture A")
            before = git("rev-parse", "HEAD")
            (repo / "revision.txt").write_text("B\n")
            git("add", ".")
            git("commit", "-m", "fixture B")
            after = git("rev-parse", "HEAD")
            git("reset", "--hard", before)
            env.update(
                NEXT_SHA=after if move_branch else before,
                REPO_URL=repo.as_uri(),
                DEST=str(root / "checkout"),
                STAGE="stg",
                BRANCH="stg",
                ROLES="base",
                OVERLAY_ID="synthetic-fixture",
            )

            def executable(name, text):
                path = bins / name
                path.write_text(text)
                path.chmod(0o700)
                return str(path)

            for name in ("timedatectl", "resolvectl", "getent", "notify"):
                executable(name, "#!/bin/sh\nexit 0\n")
            galaxy = executable(
                "galaxy",
                """#!/bin/sh
set -eu
# Advance the origin after the wrapper's initial checkout.
git -C "$DEST" rev-parse HEAD > "$INITIAL_TRACE"
git -C "$ORIGIN" update-ref refs/heads/stg "$NEXT_SHA"
""",
            )
            pull = executable(
                "pull",
                """#!/usr/bin/python3
import json, os, subprocess, sys
args=sys.argv[1:]
def arg(flag): return args[args.index(flag)+1]
dest=arg('-d'); branch=arg('-C')
subprocess.run(['git','-C',dest,'fetch','--depth','1','origin',branch], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['git','-C',dest,'reset','--hard','FETCH_HEAD'], check=True, stdout=subprocess.DEVNULL)
head=subprocess.check_output(['git','-C',dest,'rev-parse','HEAD'], text=True).strip()
with open(os.environ['TRACE'],'a') as out: out.write(json.dumps({'head':head,'args':args})+'\\n')
""",
            )
            (root / "boot-id").write_text("123e4567-e89b-12d3-a456-426614174000\n")
            source = re.sub(r"\{\{[^}]+\}\}", "1", SOURCE.read_text())
            for old, new in (
                ("/etc/ansible-pull", str(root / "etc/ansible-pull")),
                ("/run/", str(state) + "/"),
                ("/proc/sys/kernel/random/boot_id", str(root / "boot-id")),
                ("/usr/bin/ansible-galaxy", galaxy),
                ("/usr/bin/ansible-pull", pull),
                ("/usr/local/sbin/pxe-slack-notify.sh", str(bins / "notify")),
            ):
                source = source.replace(old, new)
            script = root / "wrapper.sh"
            script.write_text(source)
            result = subprocess.run(
                ["/bin/sh", str(script), "base"],
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            self.assertEqual((root / "initial-head").read_text().strip(), before)
            calls = [
                json.loads(line)
                for line in (root / "trace.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(calls), 1)
            expected = after if move_branch else before
            self.assertEqual(calls[0]["head"], expected)
            self.assertEqual(calls[0]["args"][-1], "ansible/arm64/site.yml")
            self.assertEqual(
                calls[0]["args"][calls[0]["args"].index("--limit") + 1], "base"
            )
            receipt = (state / "ansible-pull/base.post-pull.env").read_text()
            self.assertIn("CHECKOUT_HEAD_AFTER_SUCCESS=" + expected, receipt)

    def test_legacy_stationary_branch(self):
        self.run_wrapper(move_branch=False)

    def test_legacy_branch_moves_between_checkout_and_ansible(self):
        # This baseline documents why initial checkout alone does not pin execution.
        # Pinned mode must later demonstrate that it still executes A in this case.
        self.run_wrapper(move_branch=True)


if __name__ == "__main__":
    unittest.main()
