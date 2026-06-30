#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FILTER_PATH = (
    REPO_ROOT
    / "ansible/openwrt/roles/openwrt_pxe_client_catalog/filter_plugins/openwrt_pxe_client_catalog.py"
)


def load_filter_module():
    spec = importlib.util.spec_from_file_location("openwrt_pxe_client_catalog", FILTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {FILTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_k3s_stg_storage_vars() -> None:
    module = load_filter_module()
    hostvars = {
        "server1": {
            "ansible_host": "192.0.2.10",
            "k3s_local_storage_enabled": True,
            "k3s_local_storage_device": "/dev/disk/by-id/example-server-part1",
            "k3s_local_storage_node_password_sync_enabled": False,
            "k3s_local_storage_scrub_containerd_on_boot": True,
            "k3s_local_storage_scrub_containerd_confirm": "scrub-containerd-state",
            "k3s_server_node_taints": ["example.com/control-plane-only=true:NoSchedule"],
            "k3s_start_on_boot": False,
            "openwrt_pxe_client": {
                "enabled": True,
                "router": "router1",
                "roles": ["k3s_stg_server"],
                "overlay": {"id": "k3s_stg_server"},
            },
        },
        "agent1": {
            "ansible_host": "192.0.2.11",
            "k3s_local_storage_enabled": True,
            "k3s_local_storage_device": "/dev/disk/by-id/example-agent-part1",
            "k3s_local_storage_scrub_containerd_on_boot": True,
            "k3s_local_storage_scrub_containerd_confirm": "scrub-containerd-state",
            "k3s_start_on_boot": False,
            "openwrt_pxe_client": {
                "enabled": True,
                "router": "router1",
                "roles": ["k3s_stg_agent"],
                "overlay": {"id": "k3s_stg_agent1"},
            },
        },
    }
    result = module.build_openwrt_pxe_client_catalog(
        hostvars,
        {"all": ["server1", "agent1"]},
        "router1",
        None,
        None,
        None,
        True,
        [],
        [],
        [],
        "224",
        "start4.elf",
        "192.0.2.1",
        "192.0.2.1",
        1,
    )
    client_vars = {
        (item["overlay_id"], item["role"]): item["vars"]
        for item in result["ansible_pull_client_vars"]
    }
    server = client_vars[("k3s_stg_server", "k3s_stg_server")]
    agent = client_vars[("k3s_stg_agent1", "k3s_stg_agent")]

    assert server["k3s_server"]["data-dir"] == "/var/lib/rancher/k3s"
    assert server["k3s_server"]["node-taint"] == [
        "example.com/control-plane-only=true:NoSchedule"
    ]
    assert server["k3s_local_storage_enabled"] is True
    assert server["k3s_local_storage_device"] == "/dev/disk/by-id/example-server-part1"
    assert server["k3s_local_storage_ephemeral_agent_data"] is False
    assert server["k3s_local_storage_node_password_sync_enabled"] is False
    assert server["k3s_local_storage_scrub_containerd_on_boot"] is True
    assert server["k3s_local_storage_scrub_containerd_confirm"] == "scrub-containerd-state"
    assert server["k3s_start_on_boot"] is False

    assert agent["k3s_agent"]["data-dir"] == "/var/lib/rancher/k3s"
    assert agent["k3s_local_storage_enabled"] is True
    assert agent["k3s_local_storage_device"] == "/dev/disk/by-id/example-agent-part1"
    assert agent["k3s_local_storage_ephemeral_agent_data"] is True
    assert agent["k3s_local_storage_scrub_containerd_on_boot"] is True
    assert agent["k3s_local_storage_scrub_containerd_confirm"] == "scrub-containerd-state"
    assert agent["k3s_start_on_boot"] is False
    assert "k3s_local_storage_node_password_sync_enabled" not in agent


def test_host_specific_tftp_artifacts() -> None:
    module = load_filter_module()
    hostvars = {
        "node1": {
            "ansible_host": "192.0.2.20",
            "openwrt_pxe_client": {
                "enabled": True,
                "router": "router1",
                "stage": "stg",
                "pxe_host": {
                    "board_hash": "abcdef12",
                    "rpi5_kernel_image": "kernel8-nvidia.img",
                    "rpi5_initramfs": "initramfs-pxe-v8-nvidia.img",
                    "rpi5_device_tree": "bcm2712-rpi-5-b-nvidia.dtb",
                    "rpi5_pciex1_enabled": True,
                },
            },
        },
    }
    result = module.build_openwrt_pxe_client_catalog(
        hostvars,
        {"all": ["node1"]},
        "router1",
        None,
        None,
        None,
        True,
        [],
        [],
        [],
        "224",
        "start4.elf",
        "192.0.2.1",
        "192.0.2.1",
        1,
    )
    [pxe_host] = result["pxe_hosts"]
    assert pxe_host["board_hash"] == "abcdef12"
    assert pxe_host["rpi5_kernel_image"] == "kernel8-nvidia.img"
    assert pxe_host["rpi5_initramfs"] == "initramfs-pxe-v8-nvidia.img"
    assert pxe_host["rpi5_device_tree"] == "bcm2712-rpi-5-b-nvidia.dtb"
    assert pxe_host["rpi5_pciex1_enabled"] is True


def main() -> None:
    test_k3s_stg_storage_vars()
    test_host_specific_tftp_artifacts()
    print("openwrt_pxe_client_catalog checks ok")


if __name__ == "__main__":
    main()
