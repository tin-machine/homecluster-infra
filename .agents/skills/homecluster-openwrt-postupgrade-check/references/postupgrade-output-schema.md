# OpenWrt Postupgrade Collector Output Schema

The collector prints one JSON object. Treat missing fields as unknown, not healthy.

## Top-Level Fields

- `generated_at`: ISO-8601 UTC timestamp.
- `collector`: collector name and version.
- `inputs`: non-secret collection settings.
- `router`: release, package manager, packages, services, storage, firewall, PXE/NFS, routing, BGP.
- `k3s`: optional downstream k3s reachability and Kubernetes summary.
- `signals`: sanitized known error indicators.
- `assessment`: script-level heuristic result.

## `assessment.status`

- `healthy`: expected release matches, required packages and services are present, expected mounts
  are mounted, route targets do not fall through rejected devices, and optional k3s checks pass.
- `degraded`: router is reachable and on the expected release, but optional or downstream checks are
  incomplete.
- `blocked`: a known post-upgrade blocker is detected, such as missing required packages, missing
  required services, missing expected mounts, missing NFS exports, route targets falling through WAN,
  BGP command missing when route targets are configured, or k3s API unreachable when configured.
- `unknown`: insufficient data, usually router SSH failure or missing required input.

## Common Issues

- `router_ssh_unreachable`: the router could not be reached through SSH.
- `release_mismatch`: the router release differs from `OPENWRT_CHECK_EXPECTED_RELEASE`.
- `missing_required_packages`: at least one expected package is not installed.
- `missing_required_services`: at least one expected init service is missing or not running/active.
- `expected_mount_missing`: at least one expected mount target is not mounted.
- `route_target_rejected_dev`: a route target resolves through a rejected device such as WAN.
- `bgp_unavailable`: BGP checks were requested but `vtysh` or FRR output is unavailable.
- `k3s_unreachable`: optional downstream k3s control-plane checks cannot run.
- `ssh_host_key_problem`: SSH trust prevented collection. Treat this as a monitor setup issue.

## Local LLM Guidance

Prefer the collector's exact counts and failing names over optimistic interpretation. Do not paste
raw logs or long command output. Mention only short evidence snippets and the next read-only check.
