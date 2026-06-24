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
    assert server["k3s_local_storage_enabled"] is True
    assert server["k3s_local_storage_device"] == "/dev/disk/by-id/example-server-part1"
    assert server["k3s_local_storage_ephemeral_agent_data"] is False
    assert server["k3s_local_storage_node_password_sync_enabled"] is False

    assert agent["k3s_agent"]["data-dir"] == "/var/lib/rancher/k3s"
    assert agent["k3s_local_storage_enabled"] is True
    assert agent["k3s_local_storage_device"] == "/dev/disk/by-id/example-agent-part1"
    assert agent["k3s_local_storage_ephemeral_agent_data"] is True
    assert "k3s_local_storage_node_password_sync_enabled" not in agent


def main() -> None:
    test_k3s_stg_storage_vars()
    print("openwrt_pxe_client_catalog checks ok")


if __name__ == "__main__":
    main()
