#!/usr/bin/env python3
"""Fixture-only checks for staging /readyz authentication. No runtime access."""

from pathlib import Path
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/arm64/roles/k3s_networking"
TASKS = yaml.safe_load((ROLE / "tasks/main.yml").read_text())


def render(enabled: bool) -> dict:
    env = Environment(loader=FileSystemLoader(str(ROLE / "templates")), undefined=StrictUndefined)
    env.filters["bool"] = bool  # Ansible provides the production filter.
    data = env.get_template("config-server.yaml.j2").render(
        k3s_stg_readyz_anonymous_enabled=enabled,
        k3s_server={"node-ip": "192.0.2.10", "node-name": "fixture-node"},
        k3s_flannel_backend="vxlan", k3s_flannel_mtu=1400,
        ansible_host="192.0.2.10",
        ansible_facts={"default_ipv4": {"address": "192.0.2.10"}},
        inventory_hostname="fixture-node", overlay_id="fixture-node",
    )
    return yaml.safe_load(data)


def task(name: str) -> dict:
    return next(t for t in TASKS if t["name"] == name)


class ScopedReadyzTest(unittest.TestCase):
    def test_disabled_by_default(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        self.assertIs(defaults["k3s_stg_readyz_anonymous_enabled"], False)
        self.assertNotIn("kube-apiserver-arg", render(False))

    def test_enabled_renders_one_authentication_arg(self):
        self.assertEqual(
            render(True)["kube-apiserver-arg"],
            ["authentication-config=/etc/rancher/k3s/authentication-stg-readyz.yaml"],
        )

    def test_authentication_has_exactly_one_endpoint(self):
        t = task("staging /readyz 限定 AuthenticationConfiguration を配置")
        self.assertEqual(t["when"], "k3s_stg_readyz_anonymous_enabled | bool")
        self.assertEqual(t["ansible.builtin.copy"]["mode"], "0644")
        self.assertEqual(t["notify"], ["Restart k3s service"])
        auth = yaml.safe_load(t["ansible.builtin.copy"]["content"])
        self.assertEqual(
            auth,
            {"apiVersion": "apiserver.config.k8s.io/v1",
             "kind": "AuthenticationConfiguration",
             "anonymous": {"enabled": True, "conditions": [{"path": "/readyz"}]}},
        )

    def test_scope_and_version_fail_closed(self):
        t = task("staging /readyz 匿名認証を staging server のみに限定")
        checks = "\n".join(t["ansible.builtin.assert"]["that"])
        for term in ("stage | default('') == 'stg'",
                     "k3s_control_node | default(false) | bool",
                     "kube-apiserver-arg"):
            self.assertIn(term, checks)
        v = task("staging /readyz endpoint-scoped authn のサポートを確認")
        pattern = v["ansible.builtin.assert"]["that"][0].split("search('")[1].split("')")[0]
        self.assertRegex("k3s version v1.36.5+k3s1", pattern)
        self.assertNotRegex("k3s version v1.33.9+k3s1", pattern)

    def test_authentication_config_is_staged_before_server_config(self):
        names = [t["name"] for t in TASKS]
        self.assertLess(names.index("staging /readyz 限定 AuthenticationConfiguration を配置"),
                        names.index("k3s 設定ファイルを配置"))
        self.assertIn(
            "k3s_defer_service_restart | default(false) | bool",
            (ROLE / "handlers/main.yml").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
