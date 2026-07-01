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


def role_position(play: str, role_name: str) -> int:
    match = re.search(rf"- role: {re.escape(role_name)}\b", play)
    return match.start() if match else -1


def main() -> int:
    failures: list[str] = []

    adr_path = "docs/architecture-decision-record/0014-k3s-converge-wrapper-contract.md"
    defaults_path = "ansible/arm64/roles/k3s_converge_check/defaults/main.yml"
    helper_path = "ansible/arm64/roles/k3s_converge_check/files/k3s-converge"
    agent_install_config_tasks_path = (
        "ansible/arm64/roles/k3s_agent_install_config/tasks/main.yml"
    )
    agent_install_config_defaults_path = (
        "ansible/arm64/roles/k3s_agent_install_config/defaults/main.yml"
    )
    agent_install_config_template_path = (
        "ansible/arm64/roles/k3s_agent_install_config/templates/k3s-agent.service.j2"
    )
    site_path = "ansible/arm64/site.yml"

    adr = read_rel(adr_path)
    defaults = read_rel(defaults_path)
    helper = read_rel(helper_path)
    agent_install_config_tasks = read_rel(agent_install_config_tasks_path)
    agent_install_config_defaults = read_rel(agent_install_config_defaults_path)
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

    required_agent_token_default_terms = [
        "k3s_agent_install_config_token_file: /etc/rancher/k3s/cluster-token",
        'k3s_agent_install_config_control_token: ""',
    ]
    for term in required_agent_token_default_terms:
        if term not in agent_install_config_defaults:
            fail(
                f"{agent_install_config_defaults_path} must keep `{term}`",
                failures,
            )

    required_agent_token_task_terms = [
        "k3s_agent_install_config_control_token | string | length > 0",
        "path: \"{{ k3s_agent_install_config_token_file | dirname }}\"",
        "ansible.builtin.copy:",
        "dest: \"{{ k3s_agent_install_config_token_file }}\"",
        "content: \"{{ k3s_agent_install_config_control_token }}\\n\"",
        "mode: '0600'",
        "no_log: true",
    ]
    for term in required_agent_token_task_terms:
        if term not in agent_install_config_tasks:
            fail(
                f"{agent_install_config_tasks_path} must keep token placement guard `{term}`",
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

    xanmanning_match = re.search(
        r"- role: xanmanning\.k3s(?P<body>.*?)(?:\n    - role:|\n  vars:|\Z)",
        agent_play,
        flags=re.DOTALL,
    )
    xanmanning_body = xanmanning_match.group("body") if xanmanning_match else ""
    agent_install_config_position = role_position(agent_play, "k3s_agent_install_config")
    xanmanning_position = role_position(agent_play, "xanmanning.k3s")
    networking_position = role_position(agent_play, "k3s_networking")
    converge_position = role_position(agent_play, "k3s_converge_check")

    if agent_install_config_position >= 0:
        if not xanmanning_match:
            fail("k3s_agent_install_config wiring requires an explicit xanmanning.k3s agent block", failures)
        if "k3s_state: downloaded" not in xanmanning_body:
            fail("k3s_agent_install_config wiring requires agent xanmanning.k3s k3s_state: downloaded", failures)
        if "k3s_state: installed" in xanmanning_body:
            fail("k3s_agent_install_config wiring must not leave agent xanmanning.k3s at installed", failures)

        required_wired_terms = [
            "k3s_agent_install_config_enabled: true",
            "k3s_agent_install_config_release_version:",
            "k3s_agent_install_config_registration_address:",
            "k3s_agent_install_config_control_token:",
            "k3s_agent_install_config_service_name: k3s",
        ]
        for term in required_wired_terms:
            if term not in agent_play:
                fail(f"k3s_agent_install_config wiring must pass `{term}`", failures)

        expected_order = [
            ("xanmanning.k3s", xanmanning_position),
            ("k3s_agent_install_config", agent_install_config_position),
            ("k3s_networking", networking_position),
            ("k3s_converge_check", converge_position),
        ]
        missing_order_roles = [name for name, position in expected_order if position < 0]
        if missing_order_roles:
            fail(
                "k3s_agent_install_config wiring requires roles: "
                + ", ".join(missing_order_roles),
                failures,
            )
        elif not (xanmanning_position < agent_install_config_position < networking_position < converge_position):
            fail(
                "k3s agent wiring order must be xanmanning.k3s -> "
                "k3s_agent_install_config -> k3s_networking -> k3s_converge_check",
                failures,
            )

    if helper_has_lifecycle:
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
