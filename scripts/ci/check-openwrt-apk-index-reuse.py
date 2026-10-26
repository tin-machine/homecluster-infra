#!/usr/bin/env python3
"""Run the package role against a fake apk to verify play-scoped index reuse."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ApkIndexReuseTests(unittest.TestCase):
    def test_cache_hit_feed_change_and_manager_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "apk"
            config.mkdir()
            (config / "repositories").write_text("https://example.invalid/feed-a\n")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            apk = bin_dir / "apk"
            apk.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                f"  update) printf 'update\\n' >> '{root / 'updates'}' ;;\n"
                "  info) exit 0 ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n"
            )
            apk.chmod(0o755)
            role = lambda manager, names: {
                "ansible.builtin.include_role": {"name": "openwrt_package"},
                "vars": {
                    "openwrt_pkg_manager_preferred": manager,
                    "openwrt_package_names": names,
                    "openwrt_package_state": "present",
                    "openwrt_package_update_cache": True,
                    "openwrt_package_apk_config_dir": str(config),
                    "openwrt_package_apk_command": str(apk),
                },
            }
            playbook = [{
                "hosts": "localhost",
                "gather_facts": False,
                "connection": "local",
                "tasks": [
                    role("apk", ["git"]),
                    role("apk", ["git"]),
                    {"ansible.builtin.copy": {"dest": str(config / "repositories"), "content": "https://example.invalid/feed-b\n"}},
                    role("apk", ["git"]),
                    role("opkg", []),
                    role("apk", ["git"]),
                ],
            }]
            path = root / "playbook.yml"
            path.write_text(yaml.safe_dump(playbook, sort_keys=False))
            env = os.environ.copy()
            env["ANSIBLE_ROLES_PATH"] = str(ROOT / "ansible/openwrt/roles")
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", str(path)],
                env=env, text=True, capture_output=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])
            self.assertEqual((root / "updates").read_text().splitlines(), ["update"] * 3)


if __name__ == "__main__":
    unittest.main()
