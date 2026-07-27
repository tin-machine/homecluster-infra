#!/usr/bin/env python3
"""Resolve K3s SSH targets from an Ansible inventory without emitting unrelated host vars."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

DEFAULT_CONTROL_GROUP = "k3s_stg_server"
DEFAULT_AGENT_GROUP = "k3s_stg_agents"
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:%-]{0,254}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
COMMAND_TIMEOUT_SECONDS = 15


def bounded(value: Any, limit: int = 240) -> str:
    return str(value).strip().replace("\x00", "")[:limit]


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item not in result:
            result.append(item)
    return result


def group_hosts(inventory: dict[str, Any], group: str, seen: set[str] | None = None) -> list[str]:
    if seen is None:
        seen = set()
    if group in seen:
        return []
    seen.add(group)
    data = inventory.get(group)
    if not isinstance(data, dict):
        return []
    result = string_list(data.get("hosts"))
    for child in string_list(data.get("children")):
        for host in group_hosts(inventory, child, seen):
            if host not in result:
                result.append(host)
    return result


def hostvars(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    meta = inventory.get("_meta")
    if not isinstance(meta, dict):
        return {}
    raw = meta.get("hostvars")
    if not isinstance(raw, dict):
        return {}
    return {
        name: values
        for name, values in raw.items()
        if isinstance(name, str) and isinstance(values, dict)
    }


def ssh_target(host: str, values: dict[str, Any]) -> tuple[str, str]:
    if not HOST_RE.fullmatch(host):
        return "", "invalid_inventory_hostname"
    address = bounded(values.get("ansible_host", host), 255)
    user = bounded(values.get("ansible_user", "ansible"), 64)
    port = values.get("ansible_port", 22)
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        return "", "invalid_ansible_port"
    if port_number != 22:
        return "", "nonstandard_ansible_port_unsupported"
    if not VALUE_RE.fullmatch(address):
        return "", "invalid_ansible_host"
    if not USER_RE.fullmatch(user):
        return "", "invalid_ansible_user"
    return f"{user}@{address}", "ok"


def resolve_inventory(
    inventory: dict[str, Any],
    *,
    control_group: str,
    agent_group: str,
    inventory_path: str,
) -> dict[str, Any]:
    controls = group_hosts(inventory, control_group)
    agents = group_hosts(inventory, agent_group)
    if not controls:
        return {"status": "fallback_required", "reason": "control_group_empty"}
    if len(controls) != 1:
        return {"status": "fallback_required", "reason": "control_group_not_singleton"}
    if not agents and agent_group not in inventory:
        return {"status": "fallback_required", "reason": "agent_group_missing"}

    ordered_hosts: list[str] = []
    for host in controls + agents:
        if host not in ordered_hosts:
            ordered_hosts.append(host)

    all_hostvars = hostvars(inventory)
    target_map: dict[str, str] = {}
    addresses: dict[str, str] = {}
    for host in ordered_hosts:
        target, reason = ssh_target(host, all_hostvars.get(host, {}))
        if not target:
            return {
                "status": "fallback_required",
                "reason": reason,
                "failed_host": bounded(host, 128),
            }
        address = target.split("@", 1)[1]
        previous = addresses.get(address)
        if previous is not None and previous != host:
            return {
                "status": "fallback_required",
                "reason": "duplicate_ansible_host",
                "failed_host": bounded(host, 128),
            }
        addresses[address] = host
        target_map[host] = target

    control_host = controls[0]
    return {
        "status": "resolved",
        "reason": "inventory_groups_resolved",
        "inventory_path": bounded(inventory_path, 300),
        "control_group": bounded(control_group, 128),
        "agent_group": bounded(agent_group, 128),
        "control_host": control_host,
        "control_ssh": target_map[control_host],
        "node_hosts": ordered_hosts,
        "node_ssh_list": [target_map[host] for host in ordered_hosts],
        "node_target_map": target_map,
        "expected_nodes": len(ordered_hosts),
    }


def load_inventory(path: Path, command: str = "ansible-inventory") -> tuple[dict[str, Any], str]:
    try:
        completed = subprocess.run(
            [command, "-i", str(path), "--list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {}, "ansible_inventory_missing"
    except (OSError, subprocess.TimeoutExpired):
        return {}, "ansible_inventory_execution_failed"
    if completed.returncode != 0:
        return {}, "ansible_inventory_failed"
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}, "ansible_inventory_invalid_json"
    if not isinstance(value, dict):
        return {}, "ansible_inventory_invalid_contract"
    return value, "ok"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument(
        "--control-group",
        default=os.environ.get("HOMECLUSTER_K3S_CONTROL_GROUP", DEFAULT_CONTROL_GROUP),
    )
    parser.add_argument(
        "--agent-group",
        default=os.environ.get("HOMECLUSTER_K3S_AGENT_GROUP", DEFAULT_AGENT_GROUP),
    )
    args = parser.parse_args(argv)

    path = Path(args.inventory).expanduser().resolve(strict=False)
    if not path.exists():
        result = {
            "status": "fallback_required",
            "reason": "inventory_path_missing",
            "inventory_path": bounded(path, 300),
        }
    else:
        inventory, reason = load_inventory(path)
        if reason != "ok":
            result = {
                "status": "fallback_required",
                "reason": reason,
                "inventory_path": bounded(path, 300),
            }
        else:
            result = resolve_inventory(
                inventory,
                control_group=args.control_group,
                agent_group=args.agent_group,
                inventory_path=str(path),
            )

    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") == "resolved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
