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


def main() -> None:
    test_dependency_roles_are_enabled()
    test_pxe_runtime_chain_tasks()
    test_dependency_override_template()
    test_on_success_template()
    print("openwrt_pxe_ansible_pull_chain checks ok")


if __name__ == "__main__":
    main()
