#!/usr/bin/env python3
"""Fixture-only checks for staging /readyz authentication. No runtime access."""

from pathlib import Path
import re
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "ansible/arm64/roles/k3s_networking"
TASKS = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
SERVER_TASKS = yaml.safe_load((ROOT / "ansible/arm64/roles/k3s_server_install_config/tasks/main.yml").read_text())


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

    def test_version_and_scope_are_checked_in_same_tagged_role(self):
        t = task('staging /readyz 匿名認証を staging server のみに限定')
        checks = '\n'.join(t['ansible.builtin.assert']['that'])
        for term in ("stage | default('') == 'stg'",
                     'k3s_control_node | default(false) | bool',
                     'kube-apiserver-arg'):
            self.assertIn(term, checks)
        play = next(p for p in yaml.safe_load((ROOT / 'ansible/arm64/site.yml').read_text())
                    if p['name'] == 'k3s staging server')
        role = next(r for r in play['roles']
                    if isinstance(r, dict) and r.get('role') == 'k3s_networking')
        self.assertIn('k3s_networking', role['tags'])
        names = [t['name'] for t in TASKS]
        auth = names.index('staging /readyz 限定 AuthenticationConfiguration を配置')
        config = names.index('k3s 設定ファイルを配置')
        for guard in (
            'staging /readyz 匿名認証を staging server のみに限定',
            'staging /readyz 用の pinned K3s release を検証',
            'staging /readyz 用の pinned versioned binary を stat で確認',
            'staging /readyz 用の pinned versioned binary の存在を検証',
            'staging /readyz 用の pinned binary version を観測',
            'staging /readyz の pinned release と実バイナリの一致を検証',
        ):
            self.assertLess(names.index(guard), auth, guard)
            self.assertEqual(task(guard)['when'], 'k3s_stg_readyz_anonymous_enabled | bool')
        self.assertLess(auth, config)

    def test_version_is_pinned_and_rejects_unsupported_versions(self):
        pinned = task('staging /readyz 用の pinned K3s release を検証')
        assertion = pinned['ansible.builtin.assert']['that'][0]
        self.assertIn('k3s_release_version', assertion)
        pattern = assertion.split("match('")[1].split("')")[0]
        for good in ('v1.34.0+k3s1', 'v1.36.5+k3s1', 'v1.100.0+k3s1'):
            self.assertIsNotNone(re.fullmatch(pattern, good), good)
        for bad in ('false', '', 'v1.33.9+k3s1', 'v1.36.5', '../k3s', 'v2.0.0+k3s1'):
            self.assertIsNone(re.fullmatch(pattern, bad), bad)

    def test_check_mode_probes_selected_binary_not_existing_link(self):
        expected_path = ("{{ k3s_server_install_config_install_dir | default('/usr/local/bin') }}"
                         '/k3s-{{ k3s_release_version }}')
        st = task('staging /readyz 用の pinned versioned binary を stat で確認')
        self.assertEqual(st['ansible.builtin.stat']['path'], expected_path)
        self.assertIs(st['ansible.builtin.stat']['follow'], False)
        check = task('staging /readyz 用の pinned versioned binary の存在を検証')
        checks = '\n'.join(check['ansible.builtin.assert']['that'])
        self.assertIn('stat.isreg', checks)
        self.assertIn('stat.executable', checks)
        cmd = task('staging /readyz 用の pinned binary version を観測')
        self.assertEqual(cmd['ansible.builtin.command']['argv'], [expected_path, '--version'])
        self.assertIs(cmd['check_mode'], False)
        self.assertIs(cmd['changed_when'], False)
        self.assertNotIn('/k3s\n', str(cmd))
        verification = task('staging /readyz の pinned release と実バイナリの一致を検証')
        self.assertTrue(any('k3s_release_version' in v
                            for v in verification['ansible.builtin.assert']['that']))
        self.assertEqual([t['name'] for t in SERVER_TASKS].count(
            'staging /readyz opt-in 用 binary version を symlink 配置後に確認'), 0)
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
