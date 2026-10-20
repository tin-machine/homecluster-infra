#!/usr/bin/env python3
"""Synthetic boot-wrapper regression harness: local Git and mocked external tools."""

import hashlib
import json
from pathlib import Path
from jinja2 import Environment, StrictUndefined
import stat
import subprocess
import tempfile
import unittest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-ansible-pull-wrapper.sh.j2"
)


class WrapperFixture(unittest.TestCase):
    def run_wrapper(
        self,
        *,
        move_branch,
        stage="stg",
        expect_blocked=False,
        mode="legacy",
        pin_case="valid",
        mutate_tracked=False,
        ansible_rc=0,
        extra_vars=False,
        branch_override=None,
        existing_checkout=False,
        post_execution_mutation="",
    ):
        with tempfile.TemporaryDirectory(
            prefix="homecluster-boot-pin-fixture-"
        ) as name:
            root = Path(name)
            repo, bins, state = root / "origin", root / "bin", root / "run"
            repo.mkdir()
            bins.mkdir()
            state.mkdir()
            env = {"LANG": "C.UTF-8"}
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
            git("branch", "main", before)
            env.update(
                NEXT_SHA=after if move_branch else before,
                REPO_URL=repo.as_uri(),
                DEST=str(root / "checkout"),
                STAGE=stage,
                BRANCH=branch_override or ("main" if stage == "prod" else "stg"),
                MUTATE_TRACKED=str(mutate_tracked) if isinstance(mutate_tracked, str) else ("1" if mutate_tracked else "0"),
                ANSIBLE_RC=str(ansible_rc),
                POST_EXECUTION_MUTATION=post_execution_mutation,
                DECLARATION_FILE=str(root / "etc/ansible-pull/source-declaration.json"),
                ROLES="base",
                OVERLAY_ID="synthetic-fixture",
            )
            if existing_checkout:
                subprocess.run(
                    ["git", "clone", "--branch", "stg", repo.as_uri(), str(root / "checkout")],
                    env=env, check=True, capture_output=True,
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
git -C "$ORIGIN" update-ref "refs/heads/$BRANCH" "$NEXT_SHA"
case "$MUTATE_TRACKED" in
  1|staged)
    printf 'modified\n' >> "$DEST/revision.txt"
    if [ "$MUTATE_TRACKED" = staged ]; then git -C "$DEST" add revision.txt; fi
    ;;
  head)
    git -C "$DEST" fetch origin "$BRANCH"
    git -C "$DEST" reset --hard FETCH_HEAD
    ;;
esac
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
with open(os.environ['TRACE'],'a') as out: out.write(json.dumps({'head':head,'args':args,'kind':'pull'})+'\\n')
raise SystemExit(int(os.environ['ANSIBLE_RC']))
""",
            )
            playbook = executable(
                "playbook",
                """#!/usr/bin/python3
import json, os, subprocess, sys
head=subprocess.check_output(['git','-C',os.environ['DEST'],'rev-parse','HEAD'],text=True).strip()
with open(os.environ['TRACE'],'a') as out:
 out.write(json.dumps({'head':head,'args':sys.argv[1:],'kind':'playbook'})+'\\n')
if os.environ['POST_EXECUTION_MUTATION'] == 'tracked':
 with open(os.path.join(os.environ['DEST'],'revision.txt'),'a') as out: out.write('changed after execution\\n')
if os.environ['POST_EXECUTION_MUTATION'] == 'declaration':
 with open(os.environ['DECLARATION_FILE'],'a') as out: out.write(' ')
raise SystemExit(int(os.environ['ANSIBLE_RC']))
""",
            )
            declaration_path = root / "etc/ansible-pull/source-declaration.json"
            declaration_path.parent.mkdir(parents=True)
            if mode == "pinned" and pin_case != "missing":
                declaration = {
                    "schema": "homecluster-pxe-source-declaration-v1",
                    "environment": "staging",
                    "build_id": "20261018T000000Z-fixture-build-1",
                    "infra_revision": "f" * 40 if pin_case == "unavailable" else before,
                }
                if pin_case == "wrong_environment":
                    declaration["environment"] = "production"
                declaration_path.write_text(
                    "{" if pin_case == "malformed" else json.dumps(declaration)
                )
            helper_source = SOURCE.parents[1] / "files/pxe-source-pin.py"
            if helper_source.exists():
                executable(
                    "source-pin",
                    helper_source.read_text().replace(
                        "/etc/ansible-pull", str(root / "etc/ansible-pull")
                    ),
                )
            if extra_vars:
                client_vars = (
                    declaration_path.parent / "client_vars/synthetic-fixture/base.yml"
                )
                client_vars.parent.mkdir(parents=True)
                client_vars.write_text("fixture_option: true\n")
            (root / "boot-id").write_text("123e4567-e89b-12d3-a456-426614174000\n")
            source = (
                Environment(undefined=StrictUndefined, keep_trailing_newline=True)
                .from_string(SOURCE.read_text())
                .render(
                    openwrt_gentoo_k3s_stg_agent_token_wait_seconds=1,
                    openwrt_gentoo_k3s_stg_agent_token_max_age_seconds=1,
                    openwrt_gentoo_k3s_stg_agent_token_retry_seconds=1,
                    openwrt_gentoo_k3s_stg_server_api_url="",
                    openwrt_gentoo_k3s_stg_server_api_host="",
                    openwrt_gentoo_ansible_pull_source_mode=mode,
                )
            )
            for old, new in (
                ("/etc/ansible-pull", str(root / "etc/ansible-pull")),
                ("/run/", str(state) + "/"),
                ("/proc/sys/kernel/random/boot_id", str(root / "boot-id")),
                ("/usr/bin/ansible-galaxy", galaxy),
                ("/usr/bin/ansible-pull", pull),
                ("/usr/bin/ansible-playbook", playbook),
                ("/usr/local/libexec/pxe-source-pin.py", str(bins / "source-pin")),
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
            if expect_blocked:
                self.assertNotEqual(result.returncode, 0)
                if (
                    mode == "legacy"
                    or pin_case in ("missing", "malformed", "wrong_environment")
                    or stage == "prod"
                ):
                    self.assertFalse((root / "checkout").exists())
                self.assertFalse((root / "trace.jsonl").exists())
                self.assertFalse((state / "ansible-pull/base.pinned-execution.env").exists())
                return
            self.assertEqual(result.returncode, ansible_rc, result.stderr[-1000:])
            self.assertEqual((root / "initial-head").read_text().strip(), before)
            calls = [
                json.loads(line)
                for line in (root / "trace.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(calls), 1)
            expected = after if move_branch and mode == "legacy" else before
            self.assertEqual(calls[0]["head"], expected)
            self.assertEqual(
                calls[0]["kind"], "playbook" if mode == "pinned" else "pull"
            )
            expected_playbook = (
                str(root / "checkout/ansible/arm64/site.yml")
                if mode == "pinned"
                else "ansible/arm64/site.yml"
            )
            self.assertEqual(calls[0]["args"][-1], expected_playbook)
            if extra_vars:
                self.assertIn(
                    "@" + str(state / "ansible-pull/base.vars.yml"), calls[0]["args"]
                )
            if ansible_rc:
                self.assertFalse((state / "ansible-pull/base.post-pull.env").exists())
                self.assertFalse((state / "ansible-pull/base.pinned-execution.env").exists())
                return
            self.assertEqual(
                calls[0]["args"][calls[0]["args"].index("--limit") + 1], "base"
            )
            if stage == "prod":
                self.assertFalse((state / "ansible-pull/base.post-pull.env").exists())
                self.assertFalse((state / "ansible-pull/base.pinned-execution.env").exists())
                return
            receipt = (state / "ansible-pull/base.post-pull.env").read_text()
            self.assertIn("CHECKOUT_HEAD_AFTER_SUCCESS=" + expected, receipt)
            execution = state / "ansible-pull/base.pinned-execution.env"
            if post_execution_mutation:
                self.assertFalse(execution.exists())
            elif mode == "pinned":
                self.assertEqual(stat.S_IMODE(execution.stat().st_mode), 0o600)
                values = dict(line.split("=", 1) for line in execution.read_text().splitlines())
                self.assertEqual(values["SCHEMA"], "1")
                self.assertEqual(
                    values["EVIDENCE_SCOPE"],
                    "pinned_playbook_success_not_role_task_attested",
                )
                self.assertEqual(values["ROLE"], "base")
                self.assertEqual(values["BOOT_ID"], "123e4567-e89b-12d3-a456-426614174000")
                self.assertEqual(values["BUILD_ID"], "20261018T000000Z-fixture-build-1")
                self.assertEqual(values["PIN_REVISION"], before)
                self.assertEqual(values["CHECKOUT_HEAD_AFTER_SUCCESS"], before)
                self.assertEqual(
                    values["DECLARATION_SHA256"],
                    hashlib.sha256(declaration_path.read_bytes()).hexdigest(),
                )
            else:
                self.assertFalse(execution.exists())

    def test_legacy_stationary_branch(self):
        for extra_vars in (False, True):
            with self.subTest(extra_vars=extra_vars):
                self.run_wrapper(move_branch=False, extra_vars=extra_vars)

    def test_legacy_branch_moves_between_checkout_and_ansible(self):
        # This baseline documents why initial checkout alone does not pin execution.
        # Pinned mode must later demonstrate that it still executes A in this case.
        self.run_wrapper(move_branch=True)

    def test_wrong_or_unknown_stage_stops_before_checkout(self):
        for stage in ("prod", "unknown"):
            with self.subTest(stage=stage):
                self.run_wrapper(
                    move_branch=False,
                    stage=stage,
                    branch_override="stg",
                    expect_blocked=True,
                )

    def test_legacy_production_keeps_main_branch_behavior(self):
        self.run_wrapper(move_branch=False, stage="prod")

    def test_pinned_branch_movement_keeps_declared_source(self):
        self.run_wrapper(move_branch=True, mode="pinned")

    def test_pinned_stationary_branch_and_extra_vars(self):
        self.run_wrapper(move_branch=False, mode="pinned", extra_vars=True)

    def test_pinned_existing_checkout_keeps_declared_source(self):
        self.run_wrapper(move_branch=True, mode="pinned", existing_checkout=True)

    def test_pinned_missing_invalid_or_other_environment_stops(self):
        for case in ("missing", "malformed", "wrong_environment", "unavailable"):
            with self.subTest(case=case):
                self.run_wrapper(
                    move_branch=True, mode="pinned", pin_case=case, expect_blocked=True
                )

    def test_pinned_production_is_rejected_before_checkout(self):
        self.run_wrapper(
            move_branch=False, mode="pinned", stage="prod", expect_blocked=True
        )

    def test_tracked_source_change_after_galaxy_is_rejected(self):
        for mutation in (True, "staged", "head"):
            with self.subTest(mutation=mutation):
                self.run_wrapper(
                    move_branch=True, mode="pinned", mutate_tracked=mutation, expect_blocked=True
                )

    def test_pinned_ansible_failure_is_preserved_without_success_receipt(self):
        self.run_wrapper(move_branch=True, mode="pinned", ansible_rc=7)

    def test_pinned_success_with_changed_input_does_not_emit_execution_receipt(self):
        for mutation in ("tracked", "declaration"):
            with self.subTest(mutation=mutation):
                self.run_wrapper(
                    move_branch=True, mode="pinned", post_execution_mutation=mutation,
                )


if __name__ == "__main__":
    unittest.main()
