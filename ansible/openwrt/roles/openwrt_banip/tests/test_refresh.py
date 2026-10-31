"""Run actual Ansible cron updates against a disposable crontab executable.

Service operations are omitted; no system crontab or service is touched.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROLE = Path(__file__).resolve().parents[1]
OTHER = "# Existing operator job\n5 * * * * /usr/bin/true\n"


class RefreshCronTest(unittest.TestCase):
    def test_cron_behavior_and_invalid_input(self):
        with tempfile.TemporaryDirectory(prefix="banip-cron-test-") as dirname:
            root = Path(dirname)
            binary = root / "crontab"
            store = root / "fake.crontab"
            store.write_text(OTHER)
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import os,sys\nfrom pathlib import Path\n"
                "p=Path(os.environ['BANIP_TEST_CRONTAB'])\n"
                "a=sys.argv[1:]\n"
                "if '-l' in a:\n"
                " print(p.read_text(),end='');sys.exit(0)\n"
                "if '-u' in a:\n"
                " i=a.index('-u');del a[i:i+2]\n"
                "if len(a)!=1:sys.exit(2)\n"
                "p.write_bytes(Path(a[0]).read_bytes())\n"
            )
            binary.chmod(0o700)
            tasks = yaml.safe_load((ROLE / "tasks/refresh_validate.yml").read_text())
            tasks += yaml.safe_load((ROLE / "tasks/refresh.yml").read_text())
            isolated = [
                task for task in tasks
                if "ansible.builtin.assert" in task or "ansible.builtin.cron" in task
            ]
            self.assertEqual(len(isolated), 2)
            defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
            self.assertIs(defaults["openwrt_banip_refresh_enabled"], False)
            play = root / "play.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "vars": defaults, "tasks": isolated,
            }], allow_unicode=True))
            config = root / "ansible.cfg"
            config.write_text(
                "[defaults]\nhost_key_checking=True\nretry_files_enabled=False\n"
                "[local]\nbecome=False\n"
            )
            env = dict(os.environ)
            env.update({
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "BANIP_TEST_CRONTAB": str(store),
                "ANSIBLE_CONFIG": str(config),
                "ANSIBLE_NOCOLOR": "1",
            })

            def run(values=None, *, check=False, expected=0):
                variables = {"openwrt_banip_enabled": True,
                             "openwrt_banip_refresh_enabled": True}
                variables.update(values or {})
                command = ["ansible-playbook", "-i", "localhost,", str(play),
                           "-e", json.dumps(variables)]
                if check:
                    command.append("--check")
                result = subprocess.run(command, env=env, cwd=root, text=True,
                                        capture_output=True, timeout=60)
                self.assertEqual(result.returncode == 0, expected == 0,
                                 result.stdout + result.stderr)
                return result.stdout

            run(check=True)
            self.assertEqual(store.read_text(), OTHER)
            run()
            self.assertIn(OTHER, store.read_text())
            self.assertIn("17 4 * * * /etc/init.d/banip reload", store.read_text())
            self.assertEqual(store.read_text().count("#Ansible: homecluster-banip-refresh"), 1)
            self.assertIn("changed=0", run())
            for key, value in [
                ("openwrt_banip_refresh_hour", -1),
                ("openwrt_banip_refresh_hour", 24),
                ("openwrt_banip_refresh_minute", 60),
                ("openwrt_banip_refresh_hour", "4\n* * * * /bin/false"),
                ("openwrt_banip_refresh_minute", True),
                ("openwrt_banip_refresh_enabled", "yes"),
            ]:
                previous = store.read_text()
                run({key: value}, expected=1)
                self.assertEqual(store.read_text(), previous)
            run({"openwrt_banip_refresh_enabled": False})
            self.assertEqual(store.read_text(), OTHER)
            run()
            run({"openwrt_banip_enabled": False})
            self.assertEqual(store.read_text(), OTHER)


if __name__ == "__main__":
    unittest.main()
