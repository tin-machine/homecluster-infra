---
name: homecluster-convergence-monitor
description: Monitor OpenWrt/PXE/k3s staging reboot or power-cycle convergence in tin-machine/homecluster-infra using read-only collection and local LLM interpretation. Use when Codex or OpenCode is asked to watch long-running k3s staging convergence, summarize node/PXE/ansible-pull/k3s/observability readiness, classify blockers, or prepare a status report without performing repairs, SwitchBot actions, Ansible live apply, Terraform apply, sysupgrade, deletion, or secret access.
---

# Homecluster Convergence Monitor

## Scope

Use this skill to observe convergence after a reboot, power cycle, PXE/rootfs change, k3s local
storage change, or observability self-recovery event.

This skill is read-only. Do not repair the system. Do not run SwitchBot, Ansible live apply,
Terraform apply, sysupgrade, filesystem formatting, `kubectl delete`, `rm`, or any command that
changes live state.

## Workflow

1. Read `docs/ai-context.md`, `docs/full-execution-validation.md`, and
   `docs/k3s-observability-validation-checklist.md` only as needed.
2. If private operational detail is required, read the private runbook from outside this repository.
   Do not copy private hostnames, addresses, serials, raw logs, credentials, kubeconfig, or tfvars
   into this repository.
3. For a one-shot status, run:

   ```bash
   bash scripts/pi-k3s-status
   ```

   This repository entrypoint resolves `k3s_stg_server` and `k3s_stg_agents` from Ansible inventory,
   runs the collector, and attaches a public remediation document when a known case, issue, or trigger
   is detected. It has no built-in host or address list.
4. Read the result fields in this order:

   ```text
   status
   issues
   ai_case_id
   remediation_status
   remediation_title
   remediation_url
   ```

   When `remediation_status=matched`, open or return `remediation_url` before suggesting a live
   change. The document explains the background, evidence, operator gate, and response boundary.
5. Override inventory and group names only when required:

   ```text
   HOMECLUSTER_ANSIBLE_INVENTORY
   HOMECLUSTER_K3S_CONTROL_GROUP
   HOMECLUSTER_K3S_AGENT_GROUP
   ```
6. For lower-level collection with targets already supplied, run:

   ```bash
   ./.agents/skills/homecluster-convergence-monitor/scripts/collect-k3s-convergence.sh
   ```

7. Interpret the collector JSON using `references/convergence-output-schema.md`.
8. Return a concise status report with:
   - current phase,
   - blocking issue if any,
   - evidence from the collector,
   - remediation URL when matched,
   - next read-only check,
   - recommended Codex/operator action.

`pi-k3s-status` also accepts optional diagnosis and classification helpers through
`HOMECLUSTER_K3S_DIAGNOSER`, `HOMECLUSTER_K3S_CLASSIFIER`, and
`HOMECLUSTER_K3S_CASE_LIBRARY`. This allows a private companion repository to extend the public
collector without copying site-local defaults here. A detailed case ID takes priority over a generic
collector issue when selecting the remediation document.

## Adding a known case

Add these together:

1. a public-safe document under `docs/troubleshooting/`,
2. a mapping in `references/k3s-status-remediation-catalog.json`,
3. a deterministic collector, diagnoser, or classifier signal,
4. unit and fixture coverage.

Do not add a mapping without a document, or a document without a reproducible signal. Unknown cases
must remain `remediation_status=none` until evidence supports a stable classification.

## Collector Inputs

The collector intentionally has no site-local defaults. Provide inputs through environment
variables:

- `MONITOR_CONTROL_SSH`: optional SSH target for the k3s control-plane. If set, Kubernetes checks run
  through `ssh "$MONITOR_CONTROL_SSH" 'sudo -n k3s kubectl ...'`.
- `MONITOR_NODE_SSH_LIST`: optional space-separated SSH targets for node-level checks.
- `MONITOR_EXPECTED_NODES`: expected Kubernetes node count. Default: `4` for direct collector use.
  `pi-k3s-status` derives this value from inventory.
- `MONITOR_EXPECTED_NODE_EXPORTER`: expected node-exporter ready count. Default:
  `MONITOR_EXPECTED_NODES`.
- `MONITOR_OBS_NAMESPACE`: observability namespace. Default: `observability-stg`.
- `MONITOR_NODE_EXPORTER_SELECTOR`: node-exporter selector. Default:
  `app.kubernetes.io/name=prometheus-node-exporter`.
- `MONITOR_TIMEOUT_SECONDS`: collection timeout for individual shell calls. Default: `20`.

If `MONITOR_CONTROL_SSH` is not set, the collector uses local `kubectl`.

## Interpretation Rules

- Treat startup transients as non-blocking if node count, pressure, and pod readiness are improving.
- Treat these as blockers:
  - expected nodes are missing or `Ready` is not true after the expected window,
  - any node has `DiskPressure=True`, `MemoryPressure=True`, or `PIDPressure=True`,
  - non-Running/non-Succeeded pods persist,
  - Running pods have unready containers after normal startup,
  - node-exporter desired/ready is below expected,
  - k3s local storage is not mounted on nodes that should have it,
  - logs contain repeated node password / authorization / identity mismatch indicators.
- Treat `ssh_host_key_problem` as a monitoring input problem. It does not prove the cluster is
  broken; recommend Codex/operator known_hosts review before interpreting node-level checks.
- Do not expose secret values. If a command can show credentials or token material, do not run it.
- A remediation URL is guidance, not approval to mutate live state.

## Output Contract

Use this result shape in final summaries:

```text
status: converging | healthy | blocked | unknown
phase: pxe | ssh | ansible-pull | k3s | observability | steady-state
blocking_issue: <short issue or none>
evidence: <short bullets with counts and exact failing object names>
remediation_url: <public URL or none>
next_check: <read-only command or collector rerun timing>
recommended_action: <what Codex/operator should do, not what OpenCode should mutate>
```

Detailed JSON field descriptions are in `references/convergence-output-schema.md`.

For delegation to OpenCode/local LLM, use the template in
`references/opencode-task-prompt.md`.
