#!/usr/bin/env python3
from pathlib import Path
import re

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = (
    ROOT
    / "ansible/arm64/roles/common/change_make_conf/templates/make.conf.j2"
)
DEFAULTS_PATH = ROOT / "ansible/arm64/roles/distcc/defaults/main.yml"
TASKS_PATH = ROOT / "ansible/arm64/roles/distcc/tasks/main.yml"
CLI_TOOLS_PATH = ROOT / "ansible/arm64/roles/common/cli_tools/tasks/main.yml"


def ansible_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def render_expression(environment, expression, distcc):
    rendered = environment.from_string(expression).render(distcc=distcc).strip()
    return ansible_bool(rendered)


def main():
    environment = Environment(undefined=StrictUndefined)
    environment.filters["bool"] = ansible_bool

    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    client_expression = defaults["distcc_client_enabled"]
    template = environment.from_string(TEMPLATE_PATH.read_text(encoding="utf-8"))
    template_vars = {
        "common_flags_value": "-mcpu=example -mtune=example",
        "cpu_num": {"stdout": "4"},
        "gentoo_chost": "aarch64-unknown-linux-gnu",
        "gentoo_operating_mode": "standalone",
        "gentoo_binpkg_nfs_enabled": False,
    }
    cases = [
        ({}, False),
        ({"enabled": True}, True),
        ({"enabled": True, "client_enabled": False}, False),
        ({"enabled": False, "client_enabled": True}, True),
    ]
    for distcc, expected in cases:
        actual = render_expression(environment, client_expression, distcc)
        assert actual is expected, (distcc, actual, expected)

        rendered = template.render(**template_vars, distcc=distcc)
        features = next(
            line for line in rendered.splitlines() if line.startswith("FEATURES=")
        )
        tokens = features.removeprefix('FEATURES="').removesuffix('"').split()
        expected_token = "distcc" if expected else "-distcc"
        unexpected_token = "-distcc" if expected else "distcc"
        assert expected_token in tokens, (distcc, features)
        assert unexpected_token not in tokens, (distcc, features)

    tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
    tasks_by_name = {task["name"]: task for task in tasks}
    replacements = {
        name: task["ansible.builtin.replace"]
        for name, task in tasks_by_name.items()
        if "ansible.builtin.replace" in task
    }
    positive = 'FEATURES="buildpkg distcc -getbinpkg "\n'
    negative = 'FEATURES="buildpkg -distcc -getbinpkg "\n'
    enable = replacements["make.conf の distcc client を有効化"]
    disable = replacements["make.conf の distcc client を無効化"]
    assert re.sub(enable["regexp"], enable["replace"], negative, flags=re.MULTILINE) == positive
    assert re.sub(enable["regexp"], enable["replace"], positive, flags=re.MULTILINE) == positive
    assert re.sub(disable["regexp"], disable["replace"], positive, flags=re.MULTILINE) == negative
    assert re.sub(disable["regexp"], disable["replace"], negative, flags=re.MULTILINE) == negative

    remove_client = tasks_by_name["無効な Portage distcc client 用環境を削除"]
    assert remove_client["ansible.builtin.file"] == {
        "path": "/etc/portage/env/enable-distcc",
        "state": "absent",
    }
    stop_server = tasks_by_name["無効な distccd を停止して自動起動を解除"]
    assert stop_server["ansible.builtin.systemd"]["enabled"] is False
    assert stop_server["ansible.builtin.systemd"]["state"] == "stopped"
    assert any("distccd.service" in condition for condition in stop_server["when"])

    cli_tools = CLI_TOOLS_PATH.read_text(encoding="utf-8")
    assert "enable-distcc" not in cli_tools
    print("OK: ARM64 distcc client/server contract")


if __name__ == "__main__":
    main()
