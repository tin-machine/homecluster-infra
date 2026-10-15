#!/usr/bin/env python3
"""Fixture-only checks for staging /readyz authentication. No runtime access."""

from pathlib import Path
import re
import unittest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2.nativetypes import NativeEnvironment
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


def eval_assert(expr: str, **vars) -> bool:
    """Evaluate Ansible's Jinja expressions against simulated play inputs."""
    env = NativeEnvironment(undefined=StrictUndefined)
    env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    return bool(env.compile_expression(expr)(**vars))


def link_gate(tags: list[str], skipped: list[str]) -> bool:
    declaration = task("staging /readyz の実効 k3s link 検査要否を決定")
    expr = declaration["ansible.builtin.set_fact"]["k3s_stg_readyz_verify_effective_link"]
    return bool(NativeEnvironment().from_string(expr).render(
        ansible_run_tags=tags, ansible_skip_tags=skipped,
    ))


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

    def test_inventory_pin_is_captured_before_upstream_and_with_tag(self):
        play = next(p for p in yaml.safe_load((ROOT / "ansible/arm64/site.yml").read_text())
                    if p["name"] == "k3s staging server")
        roles = play["roles"]
        self.assertLess(
            next(i for i, r in enumerate(roles) if isinstance(r, dict)
                 and r.get("role") == "xanmanning.k3s"),
            next(i for i, r in enumerate(roles) if isinstance(r, dict)
                 and r.get("role") == "k3s_networking"),
        )
        guard, snapshot = play["pre_tasks"][:2]
        self.assertIn("upstream 前 inventory release pin を検証", guard["name"])
        self.assertEqual(guard["tags"], ["k3s_networking"])
        self.assertEqual(snapshot["tags"], ["k3s_networking"])
        self.assertIn("k3s_release_version",
                      snapshot["ansible.builtin.set_fact"]["k3s_stg_readyz_inventory_release_pin"])
        self.assertEqual(guard["when"],
                         "k3s_stg_readyz_anonymous_enabled | default(false) | bool")
        expr = guard["ansible.builtin.assert"]["that"][0]
        for valid in ("v1.34.0+k3s1", "v1.36.5+k3s1", "v1.100.0+k3s1"):
            self.assertTrue(eval_assert(expr, k3s_release_version=valid), valid)
        for invalid in (False, "", "v1.33.9+k3s1", "v1.36.5", "latest"):
            self.assertFalse(eval_assert(expr, k3s_release_version=invalid), str(invalid))

    def test_upstream_set_fact_cannot_forge_inventory_pin(self):
        guard = task("staging /readyz 用の upstream 前 inventory pin と実行時 release を照合")
        conditions = guard["ansible.builtin.assert"]["that"]
        self.assertEqual(len(conditions), 2)
        self.assertTrue(all(eval_assert(c,
            k3s_stg_readyz_inventory_release_pin="v1.36.5+k3s1",
            k3s_release_version="v1.36.5+k3s1",
        ) for c in conditions))
        # Upstream set_fact changes false/latest to a valid version, but the
        # earlier inventory snapshot remains invalid or mismatched.
        for prior in (False, "v1.34.0+k3s1"):
            self.assertFalse(all(eval_assert(c,
                k3s_stg_readyz_inventory_release_pin=prior,
                k3s_release_version="v1.36.5+k3s1",
            ) for c in conditions))

    def test_networking_only_tags_require_effective_link(self):
        cases = [
            (["k3s_networking"], [], True),
            (["k3s_networking"], ["k3s_server_install_config"], True),
            (["all"], [], False),
            (["k3s_stg"], [], False),
            (["k3s_stg_server"], [], False),
            (["k3s_networking", "k3s_server_install_config"], [], False),
            (["all"], ["k3s_server_install_config"], True),
            (["k3s_stg"], ["k3s_server_install_config"], True),
        ]
        for tags, skip, expected in cases:
            with self.subTest(tags=tags, skip=skip):
                self.assertIs(link_gate(tags, skip), expected)

    def test_effective_link_rejects_stale_missing_and_non_symlink(self):
        guard = task("staging /readyz の実効 k3s link が pinned binary を指すことを検証")
        checks = guard["ansible.builtin.assert"]["that"]
        expected = "/usr/local/bin/k3s-v1.36.5+k3s1"
        def allowed(link):
            return all(eval_assert(c,
                k3s_stg_readyz_effective_link={"stat": link},
                k3s_stg_readyz_inventory_release_pin="v1.36.5+k3s1",
                k3s_server_install_config_install_dir="/usr/local/bin",
            ) for c in checks)
        self.assertTrue(allowed({"islnk": True, "lnk_source": expected}))
        self.assertFalse(allowed({"islnk": True,
                                  "lnk_source": "/usr/local/bin/k3s-v1.33.9+k3s1"}))
        self.assertFalse(allowed({"islnk": False, "lnk_source": expected}))
        self.assertFalse(allowed({"exists": False}))
        st = task("staging /readyz の実効 k3s link を確認")
        self.assertIs(st["ansible.builtin.stat"]["follow"], False)
        self.assertEqual(len(st["when"]), 2)

    def test_scope_guard_and_versioned_binary_in_same_role(self):
        scope = task("staging /readyz 匿名認証を staging server のみに限定")
        checks = "\n".join(scope["ansible.builtin.assert"]["that"])
        for term in ("stage | default('') == 'stg'",
                     "k3s_control_node | default(false) | bool",
                     "kube-apiserver-arg"):
            self.assertIn(term, checks)
        names = [t["name"] for t in TASKS]
        auth = names.index("staging /readyz 限定 AuthenticationConfiguration を配置")
        for guard in (
            "staging /readyz 用の upstream 前 inventory pin と実行時 release を照合",
            "staging /readyz 用の pinned versioned binary を stat で確認",
            "staging /readyz 用の pinned versioned binary の存在を検証",
            "staging /readyz 用の pinned binary version を観測",
            "staging /readyz の pinned release と実バイナリの一致を検証",
            "staging /readyz の実効 k3s link 検査要否を決定",
            "staging /readyz の実効 k3s link を確認",
            "staging /readyz の実効 k3s link が pinned binary を指すことを検証",
        ):
            self.assertLess(names.index(guard), auth)
        # Explicit tag applies to the complete static role.
        play = next(p for p in yaml.safe_load((ROOT / "ansible/arm64/site.yml").read_text())
                    if p["name"] == "k3s staging server")
        self.assertIn("k3s_networking", next(r for r in play["roles"]
                                             if isinstance(r, dict) and r.get("role") == "k3s_networking")["tags"])

    def test_check_mode_probes_pinned_versioned_binary(self):
        expected_path = ("{{ k3s_server_install_config_install_dir | default('/usr/local/bin') }}"
                         "/k3s-{{ k3s_release_version }}")
        st = task("staging /readyz 用の pinned versioned binary を stat で確認")
        self.assertEqual(st["ansible.builtin.stat"]["path"], expected_path)
        self.assertIs(st["ansible.builtin.stat"]["follow"], False)
        ch = task("staging /readyz 用の pinned versioned binary の存在を検証")
        self.assertTrue(any("stat.isreg" in c for c in ch["ansible.builtin.assert"]["that"]))
        self.assertTrue(any("stat.executable" in c for c in ch["ansible.builtin.assert"]["that"]))
        cmd = task("staging /readyz 用の pinned binary version を観測")
        self.assertEqual(cmd["ansible.builtin.command"]["argv"], [expected_path, "--version"])
        self.assertIs(cmd["check_mode"], False)
        self.assertIs(cmd["changed_when"], False)
        verification = task("staging /readyz の pinned release と実バイナリの一致を検証")
        self.assertTrue(any("k3s_release_version" in c
                            for c in verification["ansible.builtin.assert"]["that"]))
        self.assertFalse(any("staging /readyz" in t["name"] for t in SERVER_TASKS))

    def test_versioned_binary_output_matches_pin(self):
        verification = task("staging /readyz の pinned release と実バイナリの一致を検証")
        conditions = verification["ansible.builtin.assert"]["that"]

        def accepted(output: str) -> bool:
            return all(eval_assert(condition,
                k3s_stg_readyz_binary_version={"stdout": output},
                k3s_release_version="v1.36.5+k3s1",
            ) for condition in conditions)

        self.assertTrue(accepted("k3s-v1.36.5+k3s1 version v1.36.5+k3s1 (3dd98cc5)\n"
                                 "go version go1.26.8"))
        self.assertTrue(accepted("k3s version v1.36.5+k3s1 (3dd98cc5)"))
        self.assertFalse(accepted("k3s-v1.36.4+k3s1 version v1.36.5+k3s1"))
        self.assertFalse(accepted("k3s-v1.36.5+k3s1 version v1.36.4+k3s1"))
        self.assertFalse(accepted("unrelated version v1.36.5+k3s1"))

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
