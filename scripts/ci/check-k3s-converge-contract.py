#!/usr/bin/env python3
"""Validate the staged k3s_converge source contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_rel(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def main() -> int:
    failures: list[str] = []

    adr_path = "docs/architecture-decision-record/0014-k3s-converge-wrapper-contract.md"
    defaults_path = "ansible/arm64/roles/k3s_converge_check/defaults/main.yml"
    helper_path = "ansible/arm64/roles/k3s_converge_check/files/k3s-converge"
    agent_install_config_tasks_path = (
        "ansible/arm64/roles/k3s_agent_install_config/tasks/main.yml"
    )
    agent_install_config_template_path = (
        "ansible/arm64/roles/k3s_agent_install_config/templates/k3s-agent.service.j2"
    )
    site_path = "ansible/arm64/site.yml"

    adr = read_rel(adr_path)
    defaults = read_rel(defaults_path)
    helper = read_rel(helper_path)
    agent_install_config_tasks = read_rel(agent_install_config_tasks_path)
    agent_install_config_template = read_rel(agent_install_config_template_path)
    site = read_rel(site_path)

    required_adr_terms = [
        "k3s_state: downloaded",
        "Restart k3s systemd",
        "install/config/unit/token",
        "post-merge apply",
        "SwitchBot off/on",
    ]
    for term in required_adr_terms:
        if term not in adr:
            fail(f"{adr_path} must describe `{term}` for the start-split contract", failures)

    if "k3s_converge_check_install_path: /usr/local/sbin/k3s-converge" not in defaults:
        fail("k3s_converge_check primary install path must be /usr/local/sbin/k3s-converge", failures)
    if (
        "k3s_converge_check_compat_install_path: /usr/local/sbin/k3s-converge-check"
        not in defaults
    ):
        fail("k3s_converge_check compatibility install path must remain k3s-converge-check", failures)

    if "--check-only" not in helper:
        fail("k3s-converge helper must keep --check-only support", failures)
    if "k3s-converge: check-only preconditions passed" not in helper:
        fail("k3s-converge helper success output must use the contract helper name", failures)

    lifecycle_pattern = re.compile(r"\bsystemctl\s+(start|restart|enable|disable)\b")
    helper_has_lifecycle = bool(lifecycle_pattern.search(helper))
    helper_has_start_mode = "--start" in helper or "--restart-if-needed" in helper
    if helper_has_lifecycle and not helper_has_start_mode:
        fail("k3s-converge lifecycle commands require an explicit start/restart mode", failures)

    forbidden_agent_role_terms = [
        "notify:",
        "ansible.builtin.service:",
        "ansible.builtin.systemd:",
        "ansible.builtin.command:",
        "ansible.builtin.shell:",
        "state: restarted",
        "state: started",
        "state: reloaded",
        "enabled:",
    ]
    for term in forbidden_agent_role_terms:
        if term in agent_install_config_tasks:
            fail(
                f"{agent_install_config_tasks_path} must not contain lifecycle term `{term}`",
                failures,
            )

    required_agent_unit_terms = [
        "Type=exec",
        " }}/k3s agent ",
        "--server https://{{ k3s_agent_install_config_registration_address }}:",
        "--token-file {{ k3s_agent_install_config_token_file }}",
        "--config {{ k3s_agent_install_config_config_file }}",
        "WantedBy=multi-user.target",
    ]
    for term in required_agent_unit_terms:
        if term not in agent_install_config_template:
            fail(
                f"{agent_install_config_template_path} must keep `{term}`",
                failures,
            )

    agent_play_match = re.search(
        r"- name: k3s staging agents(?P<body>.*?)(?:\n- name:|\Z)",
        site,
        flags=re.DOTALL,
    )
    if not agent_play_match:
        fail("ansible/arm64/site.yml must keep a k3s staging agents play", failures)
        agent_play = ""
    else:
        agent_play = agent_play_match.group("body")

    if "role: k3s_converge_check" not in agent_play:
        fail("k3s staging agents play must install the k3s_converge check helper", failures)

    if helper_has_lifecycle:
        xanmanning_match = re.search(
            r"- role: xanmanning\.k3s(?P<body>.*?)(?:\n    - role:|\n  vars:|\Z)",
            agent_play,
            flags=re.DOTALL,
        )
        xanmanning_body = xanmanning_match.group("body") if xanmanning_match else ""
        if not xanmanning_match:
            fail("lifecycle-enabled k3s_converge requires an explicit xanmanning.k3s agent block", failures)
        if "k3s_state: downloaded" not in xanmanning_body:
            fail("lifecycle-enabled k3s_converge requires agent xanmanning.k3s k3s_state: downloaded", failures)
        if "k3s_state: installed" in xanmanning_body:
            fail("lifecycle-enabled k3s_converge must not leave agent xanmanning.k3s at installed", failures)

    if failures:
        print("k3s_converge contract check failed", file=sys.stderr)
        for item in failures:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("k3s_converge contract checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
