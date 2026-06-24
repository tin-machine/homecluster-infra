# Convergence Collector Output Schema

The collector prints one JSON object. Treat missing fields as unknown, not healthy.

## Top-Level Fields

- `generated_at`: ISO-8601 UTC timestamp.
- `collector`: collector name and version.
- `inputs`: non-secret collection settings.
- `nodes`: Kubernetes node summary.
- `pods`: pod phase and readiness summary.
- `node_exporter`: DaemonSet and Pod readiness summary when available.
- `node_checks`: per-node SSH checks when `MONITOR_NODE_SSH_LIST` is set.
- `signals`: known log or state indicators.
- `assessment`: script-level heuristic result.

## `assessment.status`

- `healthy`: expected node count is Ready, no node pressure is true, no non-running pods, no
  running-but-unready pods, and node-exporter is at expected readiness.
- `converging`: cluster is reachable but one or more startup conditions are not yet fully ready.
- `blocked`: a known blocker is detected, such as no API, pressure, persistent non-running pods, or
  node password mismatch.
- `unknown`: insufficient data.

## Common Issues

- `ssh_host_key_problem`: SSH collection failed before node checks because known_hosts rejected one
  or more targets. Treat this as a monitor setup issue, not as cluster evidence by itself.

## Local LLM Guidance

Prefer the collector's raw counts over optimistic interpretation. If status is `converging`, include
the exact remaining object names and rerun interval. If status is `blocked`, recommend a Codex or
operator action; do not perform it.

Keep final summaries short and avoid raw logs. Include only short evidence snippets for known
indicators such as `Node password rejected`, `hash does not match`, `DiskPressure=True`, or
`ImagePullBackOff`.
