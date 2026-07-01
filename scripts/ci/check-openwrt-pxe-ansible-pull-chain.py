#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PXE_RUNTIME = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/pxe_runtime.yml"
)
OVERRIDE_TEMPLATE = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/systemd/"
    "ansible-pull@role.override.conf.j2"
)
ON_SUCCESS_TEMPLATE = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/systemd/"
    "ansible-pull@role.on-success.conf.j2"
)
HOMECLUSTER_STAGE_WRAPPER = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/"
    "homecluster-ansible-stage-wrapper.sh.j2"
)
HOMECLUSTER_STAGE_UNIT_TEMPLATE = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/systemd/"
    "homecluster-stage.service.j2"
)
UNIT_CHAIN_ADR = (
    REPO_ROOT
    / "docs/architecture-decision-record/0015-homecluster-converge-unit-chain.md"
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require_contains(text: str, needle: str, message: str) -> None:
    if needle not in text:
        raise AssertionError(message)


def require_not_contains(text: str, needle: str, message: str) -> None:
    if needle in text:
        raise AssertionError(message)


def assert_dependency_roles_are_enabled(
    roles: list[str],
    dependencies: dict[str, dict[str, Any]],
) -> None:
    enabled = set(roles)
    missing: set[str] = set()

    for role, dependency in dependencies.items():
        if role not in enabled:
            missing.add(role)
        for dep in dependency.get("after", []):
            if dep not in enabled:
                missing.add(dep)

    if missing:
        raise AssertionError(
            "openwrt_gentoo_ansible_pull_role_dependencies references roles "
            f"not listed in openwrt_gentoo_ansible_pull_roles: {sorted(missing)}"
        )


def test_dependency_roles_are_enabled() -> None:
    assert_dependency_roles_are_enabled(
        [
            "base",
            "workstation_cli",
            "k3s_stg_storage",
            "k3s_stg_server",
            "terraform_stg",
            "k3s_stg_agent",
        ],
        {
            "k3s_stg_storage": {"after": ["base"]},
            "k3s_stg_server": {"after": ["k3s_stg_storage"]},
            "k3s_stg_agent": {"after": ["k3s_stg_storage"]},
            "terraform_stg": {"after": ["k3s_stg_server"]},
        },
    )

    try:
        assert_dependency_roles_are_enabled(
            [
                "base",
                "workstation_cli",
                "k3s_stg_server",
                "terraform_stg",
                "k3s_stg_agent",
            ],
            {
                "k3s_stg_storage": {"after": ["base"]},
                "k3s_stg_server": {"after": ["k3s_stg_storage"]},
                "k3s_stg_agent": {"after": ["k3s_stg_storage"]},
                "terraform_stg": {"after": ["k3s_stg_server"]},
            },
        )
    except AssertionError as exc:
        assert "k3s_stg_storage" in str(exc)
    else:
        raise AssertionError(
            "missing k3s_stg_storage in openwrt_gentoo_ansible_pull_roles "
            "must fail validation"
        )


def test_pxe_runtime_chain_tasks() -> None:
    text = read_text(PXE_RUNTIME)

    require_contains(
        text,
        "ansible-pull dependent role の direct enable を削除",
        "pxe_runtime must remove direct multi-user enable for dependent ansible-pull roles",
    )
    require_contains(
        text,
        "not (openwrt_gentoo_homecluster_unit_chain_start_enabled | default(false) | bool)",
        "ansible-pull root role direct enable must be disabled when homecluster unit start is opted in",
    )
    require_contains(
        text,
        "homecluster unit chain start opt-in を検証",
        "pxe_runtime must validate homecluster unit start opt-in before changing the boot start point",
    )
    require_contains(
        text,
        "'base' in openwrt_gentoo_ansible_pull_roles",
        "homecluster unit start opt-in must require the base role",
    )
    require_contains(
        text,
        "selectattr('name', 'equalto', 'homecluster-base.service')",
        "homecluster unit start opt-in must require a defined homecluster-base.service",
    )
    require_contains(
        text,
        "ansible-pull root role の direct enable を削除",
        "pxe_runtime must remove ansible-pull root direct enable when homecluster-base.service is the start point",
    )
    require_contains(
        text,
        "homecluster-base.service を multi-user.target で有効化",
        "pxe_runtime must be able to opt in homecluster-base.service as the boot start point",
    )
    require_contains(
        text,
        "dest: \"{{ item }}/etc/systemd/system/multi-user.target.wants/homecluster-base.service\"",
        "homecluster-base.service opt-in must create the expected multi-user symlink",
    )
    require_contains(
        text,
        "homecluster-base.service の direct enable を削除",
        "pxe_runtime must remove homecluster-base.service direct enable when opt-in is false",
    )
    require_contains(
        text,
        "multi-user.target.wants/ansible-pull@{{ item.1.key }}.service",
        "dependent role direct-enable removal must target multi-user.target.wants",
    )
    require_contains(
        text,
        "state: absent",
        "dependent role and k3s direct-enable removals must use state: absent",
    )
    require_contains(
        text,
        "PXE rootfs の k3s direct enable を削除",
        "pxe_runtime must remove k3s direct boot enable symlinks",
    )
    require_contains(
        text,
        "product(['k3s.service', 'k3s-agent.service'])",
        "k3s direct-enable removal must cover k3s.service and k3s-agent.service",
    )
    require_contains(
        text,
        "'k3s_stg_server' in openwrt_gentoo_ansible_pull_roles or 'k3s_stg_agent' in openwrt_gentoo_ansible_pull_roles",
        "k3s direct-enable removal must be gated on k3s staging roles",
    )
    require_contains(
        text,
        "ansible-pull OnSuccess chain drop-in を配置",
        "pxe_runtime must place OnSuccess chain drop-ins",
    )
    require_contains(
        text,
        "ansible-pull OnSuccess chain の stale drop-in を削除",
        "pxe_runtime must remove stale OnSuccess drop-ins before placing current chain",
    )
    require_contains(
        text,
        "10-on-success-{{ item.2 }}.conf",
        "stale OnSuccess cleanup must target managed 10-on-success drop-ins",
    )
    require_contains(
        text,
        "10-on-success-{{ item.1.key }}.conf",
        "OnSuccess drop-ins must be named 10-on-success-<role>.conf",
    )
    require_contains(
        text,
        "ansible-pull@{{ item.1.value.after | last }}.service.d",
        "OnSuccess drop-ins must be attached to the source role unit",
    )


def test_dependency_override_template() -> None:
    text = read_text(OVERRIDE_TEMPLATE)

    require_contains(
        text,
        "{% for dep in dependency.value.after | default([]) %}",
        "dependent role override must iterate dependency.value.after",
    )
    require_contains(
        text,
        "After=ansible-pull@{{ dep }}.service",
        "dependent role override must order after each dependency",
    )
    require_not_contains(
        text,
        "Requires=",
        "dependent role override must not use Requires=; dependency failure drops queued jobs",
    )


def test_on_success_template() -> None:
    text = read_text(ON_SUCCESS_TEMPLATE)

    require_contains(
        text,
        "OnSuccess=ansible-pull@{{ dependency.key }}.service",
        "OnSuccess template must enqueue the dependent role unit",
    )
    require_not_contains(
        text,
        "Requires=",
        "OnSuccess template must not reintroduce Requires=",
    )


def test_homecluster_stage_wrapper_template() -> None:
    text = read_text(HOMECLUSTER_STAGE_WRAPPER)

    required_terms = [
        "role_for_stage()",
        "base)",
        "storage)",
        "k3s|k3s-converge)",
        "terraform)",
        "k3s_stg_server",
        "k3s_stg_agent",
        "exec /usr/local/sbin/pxe-ansible-pull-wrapper.sh",
        "not in ROLES",
    ]
    for term in required_terms:
        require_contains(
            text,
            term,
            f"{HOMECLUSTER_STAGE_WRAPPER.relative_to(REPO_ROOT)} must keep `{term}`",
        )


def test_homecluster_stage_unit_template() -> None:
    text = read_text(HOMECLUSTER_STAGE_UNIT_TEMPLATE)

    required_terms = [
        "OnSuccess={{ homecluster_stage_unit.next }}",
        "ConditionPathExists=/run/dhcp/role.env",
        "EnvironmentFile=-/run/dhcp/role.env",
        "ExecStart=/usr/local/sbin/homecluster-ansible-stage-wrapper.sh",
        "WantedBy=multi-user.target",
    ]
    for term in required_terms:
        require_contains(
            text,
            term,
            f"{HOMECLUSTER_STAGE_UNIT_TEMPLATE.relative_to(REPO_ROOT)} must keep `{term}`",
        )
    require_not_contains(
        text,
        "Requires=",
        "homecluster domain unit template must not use Requires= before retry semantics are explicit",
    )


def test_homecluster_unit_chain_is_not_direct_enabled() -> None:
    text = read_text(PXE_RUNTIME)

    require_contains(
        text,
        "homecluster domain unit を配置",
        "pxe_runtime must place source-only homecluster domain units",
    )
    require_contains(
        text,
        "openwrt_gentoo_homecluster_unit_chain_start_enabled | default(false) | bool",
        "homecluster domain start point must remain behind explicit opt-in",
    )
    require_not_contains(
        text,
        "multi-user.target.wants/{{ item.1.name }}",
        "homecluster domain units other than the explicit start point must not be direct enabled",
    )


def test_homecluster_unit_chain_adr_contract() -> None:
    text = read_text(UNIT_CHAIN_ADR)

    required_terms = [
        "homecluster-base.service",
        "homecluster-storage.service",
        "homecluster-k3s-converge.service",
        "homecluster-terraform.service",
        "ansible-pull@base.service",
        "k3s_stg_storage",
        "OnSuccess=",
        "Requires=",
        "direct boot enable",
        "integrated iSCSI verifier",
        "SwitchBot off/on",
    ]
    for term in required_terms:
        require_contains(
            text,
            term,
            f"{UNIT_CHAIN_ADR.relative_to(REPO_ROOT)} must document `{term}`",
        )


def main() -> None:
    test_dependency_roles_are_enabled()
    test_pxe_runtime_chain_tasks()
    test_dependency_override_template()
    test_on_success_template()
    test_homecluster_stage_wrapper_template()
    test_homecluster_stage_unit_template()
    test_homecluster_unit_chain_is_not_direct_enabled()
    test_homecluster_unit_chain_adr_contract()
    print("openwrt_pxe_ansible_pull_chain checks ok")


if __name__ == "__main__":
    main()
