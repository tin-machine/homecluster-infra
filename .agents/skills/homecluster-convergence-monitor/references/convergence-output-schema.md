# Convergence Collector and Status Output Schema

The lower-level collector prints one JSON object. Treat missing fields as unknown, not healthy.
The repository entrypoint `bash scripts/pi-k3s-status` converts that collector result into the
one-shot status contract and adds an optional public remediation document.

## Collector Top-Level Fields

- `generated_at`: ISO-8601 UTC timestamp.
- `collector`: collector name and version.
- `inputs`: non-secret collection settings.
- `nodes`: Kubernetes node summary.
- `pods`: pod phase and readiness summary.
- `node_exporter`: DaemonSet and Pod readiness summary when available.
- `node_checks`: per-node SSH checks when `MONITOR_NODE_SSH_LIST` is set.
- `signals`: known log or state indicators.
- `assessment`: script-level heuristic result.

## `node_exporter` readiness semantics

`node_exporter.desired` is the **effective desired count**:

```text
max(DaemonSet.status.desiredNumberScheduled, MONITOR_EXPECTED_NODE_EXPORTER)
```

`MONITOR_EXPECTED_NODE_EXPORTER` is therefore an optional floor, not an on/off switch. The
repository status entrypoint currently sets the floor to `0`, which means the live DaemonSet desired
count remains authoritative.

`node_exporter_not_ready` is emitted when `node_exporter.ready < node_exporter.desired`.

During a DaemonSet rolling update, more Ready Pods than the current desired count can exist
transiently. For example, `ready=3` and `desired=2` is valid and does not itself create an issue.
Do not clamp the Ready count to desired; it is an observed Pod count.

## `assessment.status`

- `healthy`: expected node count is Ready, no node pressure is true, no non-running pods, no
  running-but-unready pods, and node-exporter is at effective desired readiness.
- `converging`: cluster is reachable but one or more startup conditions are not yet fully ready.
- `blocked`: a known blocker is detected, such as no API, pressure, persistent non-running pods, or
  node identity mismatch.
- `unknown`: insufficient data.

## Common Issues

- `nodes_not_ready`: at least one discovered node is not Ready.
- `missing_expected_nodes`: discovered node count is below the inventory-derived expectation.
- `node_exporter_not_ready`: Ready node-exporter Pods are below the effective desired count.
- `node_identity_signal`: a node password, hash, or authorization signature is still actionable.
- `ssh_host_key_problem`: SSH collection failed before node checks because known_hosts rejected one
  or more targets. Treat this as a monitor setup issue, not as cluster evidence by itself.

## Repository Status Contract

`bash scripts/pi-k3s-status --json` returns the normalized status fields plus:

```json
{
  "remediation": {
    "status": "matched",
    "match_key": "case:k3s_agent_registration_auth_mismatch",
    "id": "k3s-agent-registration-auth-mismatch",
    "title": "k3s agent registration authentication mismatch",
    "url": "https://github.com/tin-machine/homecluster-infra/blob/main/docs/troubleshooting/k3s-agent-registration-auth-mismatch.md",
    "catalog_version": "2026-07-22.1"
  }
}
```

`remediation.status` is:

- `matched`: a public document is mapped to the most specific known case, candidate, issue, or
  trigger.
- `none`: no stable public mapping exists. Do not guess a remediation document.

Selection priority is:

1. classified `ai_analysis.case_id`,
2. secondary case IDs,
3. rule candidates,
4. collector issues,
5. diagnostic triggers.

The text output exposes the same information as:

```text
remediation_status=matched|none
remediation_match_key=<case, issue, or trigger key|none>
remediation_id=<stable document ID|none>
remediation_title=<title|none>
remediation_url=<public GitHub URL|none>
remediation_catalog_version=<version>
```

A matched URL is guidance, not approval to mutate live state. The linked document must retain its
operator gate and secret boundary.

## Local LLM Guidance

Prefer the collector's raw counts over optimistic interpretation. If status is `converging`, include
the exact remaining object names and rerun interval. If status is `blocked`, return the matched
`remediation_url` when present and recommend a Codex or operator action; do not perform it.

Keep final summaries short and avoid raw logs. Include only short evidence snippets for known
indicators such as `Node password rejected`, `hash does not match`, `DiskPressure=True`, or
`ImagePullBackOff`.
