#!/usr/bin/env python3
"""Keep server-only workers and dynamic Portage parallelism linked."""

import ast
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs"


def task_by_name(path, name):
    return next(task for task in yaml.safe_load(path.read_text()) if task["name"] == name)


def main():
    preflight = ROLE / "tasks/distcc_preflight.yml"
    setup = ROLE / "tasks/distcc_setup.yml"
    environment = Environment(undefined=StrictUndefined)
    environment.filters["bool"] = bool

    selected = task_by_name(preflight, "利用可能な distcc worker だけを採用")
    expression = selected["ansible.builtin.set_fact"]["openwrt_gentoo_distcc_usable_names"]
    rendered = environment.from_string(expression).render(
        openwrt_gentoo_distcc_client_probes={"results": [
            {"item": "stg", "failed": False, "state": "started"},
            {"item": "prod", "failed": False, "state": "started"},
            {"item": "offline", "failed": True, "state": "started"},
        ]}
    )
    assert ast.literal_eval(rendered.strip()) == ["stg", "prod"]

    hosts = task_by_name(preflight, "利用可能な distcc hosts 文字列を作成")
    expression = hosts["ansible.builtin.set_fact"]["openwrt_gentoo_distcc_hosts_effective"]
    rendered = environment.from_string(expression).render(
        openwrt_gentoo_distcc_usable_names=["stg", "prod"],
        openwrt_gentoo_distcc_default_slots=5,
        hostvars={
            "stg": {"ansible_host": "192.0.2.10", "distcc": {"enabled": True, "slots": 2}},
            "prod": {"ansible_host": "192.0.2.20", "distcc": {
                "enabled": True, "client_enabled": False, "slots": 2,
            }},
        },
    )
    assert rendered.strip() == "192.0.2.10/2 192.0.2.20/2"

    port_probe = task_by_name(preflight, "OpenWrt から worker の distccd port への接続を確認")
    assert "ansible.builtin.wait_for" in port_probe
    slots = task_by_name(setup, "各 build root で distcc -j を取得")
    assert slots["ansible.builtin.command"]["argv"][-2:] == ["/usr/bin/distcc", "-j"]
    final = task_by_name(setup, "全 Portage package 用の動的 MAKEOPTS を設定")
    assert "-j{{ openwrt_gentoo_distcc_slots_results" in final["ansible.builtin.set_fact"]["openwrt_gentoo_makeopts_effective"]
    template = (ROLE / "templates/portage/make.conf.j2").read_text()
    assert 'MAKEOPTS="{{ openwrt_gentoo_makeopts_effective | default(openwrt_gentoo_makeopts) }}"' in template

    prod_play = next(play for play in yaml.safe_load((ROOT / "ansible/arm64/site.yml").read_text())
                     if play.get("name") == "k3s production base")
    role = next(role for role in prod_play["roles"] if isinstance(role, dict) and role.get("role") == "distcc")
    assert role["when"] == "'k3s_prd' in group_names"
    print("OK: OpenWrt distcc runtime contract")


if __name__ == "__main__":
    main()
