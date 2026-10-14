#!/usr/bin/env python3
"""Exercise the pre-mutation identity/format guard with fixed local cases."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "scripts/ansible/fixtures/k3s_local_storage_identity_format_guard.yml"
BASE = {
    "k3s_local_storage_enabled": True,
    "k3s_local_storage_node_password_sync_enabled": True,
    "k3s_local_storage_force_format": False,
    "k3s_local_storage_ephemeral_agent_data": False,
}
CASES = (
    ("persistent_identity", {}, True),
    ("force_format_wipes_identity", {"k3s_local_storage_force_format": True}, False),
    ("ephemeral_wipes_identity", {"k3s_local_storage_ephemeral_agent_data": True}, False),
    ("no_password_sync", {
        "k3s_local_storage_node_password_sync_enabled": False,
        "k3s_local_storage_force_format": True,
    }, True),
    ("storage_disabled", {
        "k3s_local_storage_enabled": False,
        "k3s_local_storage_force_format": True,
    }, True),
)


def main() -> int:
    failures = []
    for name, changes, should_pass in CASES:
        variables = {**BASE, **changes}
        result = subprocess.run(
            [
                "ansible-playbook", "-i", "localhost,", "-c", "local",
                "--tags", "k3s_identity_format_guard", "-e", json.dumps(variables),
                str(PLAYBOOK),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        passed = result.returncode == 0
        if passed != should_pass or (
            not should_pass and "node password 同期が有効" not in result.stdout
        ):
            failures.append({
                "case": name,
                "exit_code": result.returncode,
                "stdout_tail": result.stdout[-1000:],
                "stderr_tail": result.stderr[-500:],
            })
    print(json.dumps({"ok": not failures, "cases": len(CASES), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
