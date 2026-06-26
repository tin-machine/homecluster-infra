---
name: homecluster-openwrt-postupgrade-check
description: Collect and interpret read-only OpenWrt post-upgrade health evidence for tin-machine/homecluster-infra. Use when Codex or OpenCode is asked to check an OpenWrt sysupgrade result, package/service restoration, storage mounts, PXE/TFTP/NFS readiness, FRR/BGP routes, firewall/banIP/exporter status, or downstream k3s reachability without performing repairs, Ansible live apply, sysupgrade, reboot, deletion, formatting, or secret access.
---

# Homecluster OpenWrt Postupgrade Check

## Scope

Use this skill after an OpenWrt sysupgrade or rollback to classify whether the router itself and
the services it provides to PXE/k3s have recovered.

This skill is read-only. Do not repair the system. Do not run Ansible live apply, sysupgrade,
reboot, SwitchBot, service restart, package install, filesystem formatting, `kubectl delete`, `rm`,
or any command that changes live state.

## Workflow

1. Read `docs/ai-context.md` only as needed for public repository boundaries.
2. If site-local hostnames, addresses, BGP peers, route targets, or private run history are needed,
   read the private runbook from outside this repository. Do not copy those values into this
   repository.
3. Run the collector script:

   ```bash
   ./.agents/skills/homecluster-openwrt-postupgrade-check/scripts/collect-openwrt-postupgrade.sh
   ```

4. Interpret the JSON output using
   `references/postupgrade-output-schema.md`.
5. Return a concise status report with:
   - router release and package/service recovery,
   - mount, route, BGP, firewall, PXE/NFS signals,
   - downstream k3s reachability if configured,
   - blocking issue if any,
   - next read-only check,
   - recommended Codex/operator action.

## Collector Inputs

The collector intentionally has no site-local defaults. Provide inputs through environment
variables:

- `OPENWRT_CHECK_ROUTER_SSH`: SSH target for the OpenWrt router. Required for router checks.
- `OPENWRT_CHECK_EXPECTED_RELEASE`: expected OpenWrt release, such as `24.10.7`.
- `OPENWRT_CHECK_REQUIRED_PACKAGES`: space-separated packages expected after restoration.
- `OPENWRT_CHECK_REQUIRED_SERVICES`: space-separated init services expected after restoration.
- `OPENWRT_CHECK_EXPECTED_MOUNTS`: space-separated mount targets expected on the router.
- `OPENWRT_CHECK_ROUTE_TARGETS`: space-separated IPs or prefixes that should route correctly.
- `OPENWRT_CHECK_ROUTE_REJECT_DEVS`: route devices that should not carry those route targets.
  Default: `pppoe-wan wan`.
- `OPENWRT_CHECK_K3S_CONTROL_SSH`: optional SSH target for k3s control-plane reachability checks.
- `OPENWRT_CHECK_OBS_NAMESPACE`: observability namespace. Default: `observability-stg`.
- `OPENWRT_CHECK_TIMEOUT_SECONDS`: timeout for individual commands. Default: `20`.

## Interpretation Rules

- Treat the router as not recovered if the expected release does not match.
- Treat missing post-upgrade packages or missing service init scripts as `blocked` for router
  feature recovery.
- Treat missing `/srv`-class mounts, missing NFS exports, or route targets falling through WAN as
  `blocked` for PXE/k3s recovery.
- Treat k3s SSH or Kubernetes API failures as downstream reachability blockers only when k3s inputs
  were provided.
- Treat `ssh_host_key_problem` as a monitor setup issue. It does not prove the router is broken;
  recommend Codex/operator known_hosts review before interpreting live state.
- Do not expose secret values. If a command can show credentials, token material, kubeconfig,
  tfvars, private keys, or raw backup contents, do not run it.

## Collector Implementation Notes

The collector's `run_router` function returns a JSON wrapper from `capture_json`, not raw command
stdout. Do not parse variables such as `release_raw`, `pkg_mgr_raw`, or `packages_raw` with plain
`grep`, `cut`, or shell parameter expansion unless they were first decoded with `jq`.

Router state must be detected on the router. Do not use local `command -v apk`, `command -v opkg`,
or local `/etc/openwrt_release` checks to decide router package-manager behavior. Put those checks
inside a `run_router '...'` command, or decode a prior `run_router` JSON result with `jq`.

For package-manager coexistence, record both:

- commands detected on the router, from `pkg_mgr_raw`;
- the package manager actually used to collect package names.

## Output Contract

Use this result shape in final summaries:

```text
status: healthy | degraded | blocked | unknown
phase: router-ssh | release | package-restore | service-restore | storage | routing | k3s | steady-state
blocking_issue: <short issue or none>
evidence: <short bullets with counts and exact failing names>
next_check: <read-only command or collector rerun timing>
recommended_action: <what Codex/operator should do, not what OpenCode should mutate>
```

Detailed JSON field descriptions are in `references/postupgrade-output-schema.md`.

For delegation to OpenCode/local LLM, use the template in
`references/opencode-task-prompt.md`.
