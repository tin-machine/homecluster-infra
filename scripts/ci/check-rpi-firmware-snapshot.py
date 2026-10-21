#!/usr/bin/env python3
"""Exercise the TFTP firmware tasks against a synthetic, drifting release."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = (
    ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/tftp_artifacts.yml",
    ROOT / "ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/tftp_overlays.yml",
)
FILES = (
    "start4.elf",
    "start.elf",
    "fixup4.dat",
    "fixup.dat",
    "bootcode.bin",
    "kernel8.img",
    "kernel8_rt.img",
    "kernel_2712.img",
    "bcm2711-rpi-4-b.dtb",
    "bcm2712-rpi-5-b.dtb",
    "overlays/overlay_map.dtb",
    "overlays/bcm2712d0.dtbo",
)


def firmware_tasks() -> list[dict]:
    selected = []
    for path in TASKS:
        for task in yaml.safe_load(path.read_text(encoding="utf-8")):
            name = task.get("name", "")
            if name.startswith("TFTP ") and (
                "ファイルを取得" in name or "modules と同じ checkout から配置" in name
            ):
                selected.append(task)
    if len(selected) != 8:
        raise AssertionError(f"expected four get_url/copy pairs, got {len(selected)}")
    return selected


def run_fixture(root: Path, tasks: list[dict], missing_kernel: bool) -> int:
    checkout = root / "checkout"
    tftp = root / "tftp"
    for name in FILES:
        source = checkout / "boot" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(("snapshot:" + name).encode())
        target = tftp / ("firmware/" + name if name in FILES[:5] else name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"newer-remote-kernel")
    if missing_kernel:
        (checkout / "boot/kernel8.img").unlink()

    playbook = root / "fixture.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        "openwrt_gentoo_enabled": True,
                        "openwrt_gentoo_copy_rpi_modules": True,
                        "openwrt_gentoo_tftp_fetch_firmware": True,
                        "openwrt_gentoo_tftp_firmware_dir": "firmware",
                        "openwrt_gentoo_firmware_checkout_dir": str(checkout),
                        "openwrt_gentoo_tftp_dir": str(tftp),
                        "openwrt_gentoo_tftp_firmware_base_url": "http://127.0.0.1:9/forbidden",
                        "openwrt_gentoo_tftp_get_url_timeout": 1,
                        "openwrt_gentoo_tftp_get_url_retries": 1,
                        "openwrt_gentoo_tftp_get_url_delay": 0,
                    },
                    "tasks": tasks,
                }
            ],
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if missing_kernel:
        if result.returncode == 0:
            raise AssertionError("missing checkout kernel did not fail closed")
        if (tftp / "kernel8.img").read_bytes() != b"newer-remote-kernel":
            raise AssertionError("missing checkout kernel changed the TFTP target")
    else:
        if result.returncode != 0:
            raise AssertionError(result.stdout[-3000:] + result.stderr[-1000:])
        for name in FILES:
            target = tftp / ("firmware/" + name if name in FILES[:5] else name)
            if target.read_bytes() != ("snapshot:" + name).encode():
                raise AssertionError(f"TFTP artifact does not match checkout: {name}")
    return result.returncode


def main() -> None:
    tasks = firmware_tasks()
    with tempfile.TemporaryDirectory(prefix="rpi-firmware-snapshot-") as temp:
        root = Path(temp)
        run_fixture(root / "normal", tasks, missing_kernel=False)
        run_fixture(root / "missing", tasks, missing_kernel=True)
    print("rpi firmware snapshot: pass")


if __name__ == "__main__":
    main()
