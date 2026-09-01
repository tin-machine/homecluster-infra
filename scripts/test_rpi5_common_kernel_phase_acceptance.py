from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "ansible/openwrt/playbooks/rpi5-common-kernel-phase-acceptance.yml"
SAFE_ROUTE_CHECK = 'test -n "$(ip route show)"'
UNSAFE_ROUTE_CHECK = "ip route show | grep -q ."


class CommonKernelPhaseAcceptanceTests(unittest.TestCase):
    def route_check(self) -> str:
        matches = [
            line.strip()
            for line in PLAYBOOK.read_text(encoding="utf-8").splitlines()
            if "ip route show" in line
        ]
        self.assertEqual(matches, [SAFE_ROUTE_CHECK])
        self.assertNotIn(UNSAFE_ROUTE_CHECK, PLAYBOOK.read_text(encoding="utf-8"))
        return matches[0]

    def run_route_check(self, output_lines: int) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ip = directory / "ip"
            ip.write_text(
                "#!/bin/sh\n"
                "test \"$*\" = \"route show\" || exit 2\n"
                f"count={output_lines}\n"
                "index=0\n"
                "while [ \"$index\" -lt \"$count\" ]; do\n"
                "  printf 'default via 192.0.2.1 dev eth0\\n'\n"
                "  index=$((index + 1))\n"
                "done\n",
                encoding="utf-8",
            )
            ip.chmod(0o755)
            environment = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"}
            return subprocess.run(
                ["bash", "-o", "pipefail", "-c", self.route_check()],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

    def test_many_routes_pass_without_sigpipe(self):
        completed = self.run_route_check(4096)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_route_fails(self):
        completed = self.run_route_check(0)
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
