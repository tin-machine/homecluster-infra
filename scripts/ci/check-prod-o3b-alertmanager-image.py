#!/usr/bin/env python3
"""Validate the pinned Alertmanager image/config without network or real secrets."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prod_o3b_alertmanager_render as renderer


def main() -> int:
    manifest = subprocess.run(
        ["docker", "manifest", "inspect", renderer.IMAGE],
        capture_output=True, text=True, timeout=45, check=True,
    )
    document = json.loads(manifest.stdout)
    arm64 = any(item.get("platform", {}).get("os") == "linux" and
                item.get("platform", {}).get("architecture") == "arm64"
                for item in document.get("manifests", []))
    if not arm64:
        raise SystemExit("pinned Alertmanager image has no linux/arm64 manifest")

    with tempfile.TemporaryDirectory(prefix="prod-o3b-alertmanager-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        config = root / "alertmanager.json"
        config.write_text(json.dumps(renderer.alertmanager_config("C0123456789")))
        config.chmod(0o644)
        token = root / "token"
        token.write_text("synthetic-ci-token\n")
        token.chmod(0o644)
        args = [
            "docker", "run", "--rm", "--pull=always", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--mount", f"type=bind,source={root},target=/etc/alertmanager,readonly",
            "--mount", f"type=bind,source={root},target=/etc/alertmanager-secret,readonly",
            "--entrypoint=/bin/amtool", renderer.IMAGE,
            "check-config", "/etc/alertmanager/alertmanager.json",
        ]
        checked = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if checked.returncode != 0:
            print("Alertmanager image config validation failed", file=sys.stderr)
            print(checked.stderr[-1200:], file=sys.stderr)
            return 1
    print("prod O3-B Alertmanager pinned image arm64/config smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
