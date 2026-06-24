# OpenCode Task Prompt Template

Use this template when delegating long convergence monitoring to OpenCode/local LLM.

Do not include token values, kubeconfig content, private inventory content, or raw secret material.
Replace placeholders at runtime or export them in the shell environment before invoking OpenCode.

```text
Use $homecluster-convergence-monitor to monitor the current k3s staging convergence.

Scope:
- Read-only monitoring only.
- Run the collector script and interpret its JSON output.
- Do not run SwitchBot, Ansible live apply, Terraform apply, sysupgrade, kubectl delete, rm,
  filesystem format, service restart, or any other mutating command.
- Do not print token, kubeconfig, tfvars, password, private key, or secret values.

Collector command:

MONITOR_CONTROL_SSH=<control-plane-ssh-target> \
MONITOR_NODE_SSH_LIST='<node-ssh-target-1> <node-ssh-target-2> <node-ssh-target-3> <node-ssh-target-4>' \
MONITOR_EXPECTED_NODES=4 \
MONITOR_EXPECTED_NODE_EXPORTER=4 \
MONITOR_TIMEOUT_SECONDS=20 \
  ./.agents/skills/homecluster-convergence-monitor/scripts/collect-k3s-convergence.sh

Return:
- status: healthy | converging | blocked | unknown
- phase: pxe | ssh | ansible-pull | k3s | observability | steady-state
- blocking_issue: none or one short issue
- evidence: concise bullets with counts and object names
- next_check: when and what read-only check should run next
- recommended_action: what Codex/operator should consider; do not perform it yourself
```
