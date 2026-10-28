#!/usr/bin/env python3
"""Check a Terraform JSON plan for state adoption with unchanged live object values."""
from __future__ import annotations

import json
import argparse
import sys

from prod_prometheus_config_render import IDENTITIES as PROMETHEUS_IDENTITIES

IDENTITIES = {
    "ConfigMap/prod-grafana-provisioning",
    "Deployment/prod-grafana",
    "Service/prod-grafana",
    "NetworkPolicy/prod-grafana-restricted",
    "NetworkPolicy/prod-prometheus-grafana-ingress",
}


def address(name: str) -> str:
    resource = "workload" if name.startswith("Deployment/") else "grafana"
    return f'kubernetes_manifest.{resource}["{name}"]'


def unknown(value: object) -> bool:
    if isinstance(value, dict):
        return any(unknown(v) for v in value.values())
    if isinstance(value, list):
        return any(unknown(v) for v in value)
    return value is True


def check(plan: dict, *, include_prometheus: bool = False) -> dict:
    identities = IDENTITIES | PROMETHEUS_IDENTITIES if include_prometheus else IDENTITIES
    expected = {address(name) for name in identities}
    resources = [r for r in plan.get("resource_changes", []) if r.get("mode") == "managed"]
    if {r["address"] for r in resources} != expected or len(resources) != len(expected):
        raise ValueError("unexpected_resource_set")
    updates = 0
    for resource in resources:
        change = resource["change"]
        if change["actions"] not in (["no-op"], ["update"]):
            raise ValueError("create_delete_or_replace_forbidden")
        if unknown(change.get("after_unknown", {})):
            raise ValueError("unknown_planned_values")
        before, after = change["before"], change["after"]
        if before.get("object") != after.get("object"):
            raise ValueError("live_object_values_change")
        for key in set(before) | set(after):
            if key not in ("manifest", "field_manager", "wait", "timeouts") and before.get(key) != after.get(key):
                raise ValueError("unexpected_state_change")
        if after.get("field_manager") != [{"name": "terraform-prod-grafana", "force_conflicts": False}]:
            raise ValueError("unexpected_field_manager")
        if resource["address"].startswith("kubernetes_manifest.workload["):
            if after.get("wait") != [{"condition": [], "fields": None, "rollout": True}]:
                raise ValueError("unexpected_rollout_wait")
            if after.get("timeouts") != [{"create": None, "delete": None, "update": "5m"}]:
                raise ValueError("unexpected_rollout_timeout")
        elif before.get("wait") != after.get("wait") or before.get("timeouts") != after.get("timeouts"):
            raise ValueError("unexpected_resource_wait")
        if change["actions"] == ["update"]:
            updates += 1
    return {"status": "pass", "resources": len(resources), "state_adoption_updates": updates,
            "live_object_values_unchanged": True, "applied": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-prometheus", action="store_true")
    args = parser.parse_args()
    try:
        result = check(json.load(sys.stdin), include_prometheus=args.include_prometheus)
    except (ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
