# OpenCode Task Prompt Template

Use this template when delegating OpenWrt post-upgrade checks to OpenCode/local LLM.

Do not include token values, kubeconfig content, private inventory content, raw backup contents, or
secret material. Replace placeholders at runtime or export them in the shell environment before
invoking OpenCode.

```text
Use $homecluster-openwrt-postupgrade-check to classify the current OpenWrt post-upgrade state.

Scope:
- Read-only monitoring only.
- Run the collector script and interpret its JSON output.
- Do not run Ansible live apply, sysupgrade, reboot, SwitchBot, package install, service restart,
  kubectl delete, rm, filesystem format, or any other mutating command.
- Do not print token, kubeconfig, tfvars, password, private key, backup contents, or secret values.

Collector command:

OPENWRT_CHECK_ROUTER_SSH=<router-ssh-target> \
OPENWRT_CHECK_EXPECTED_RELEASE=<expected-release> \
OPENWRT_CHECK_REQUIRED_PACKAGES='<package-1> <package-2> ...' \
OPENWRT_CHECK_REQUIRED_SERVICES='<service-1> <service-2> ...' \
OPENWRT_CHECK_EXPECTED_MOUNTS='<mount-1> <mount-2> ...' \
OPENWRT_CHECK_ROUTE_TARGETS='<route-target-1> <route-target-2>' \
OPENWRT_CHECK_K3S_CONTROL_SSH=<k3s-control-plane-ssh-target> \
OPENWRT_CHECK_TIMEOUT_SECONDS=20 \
  ./.agents/skills/homecluster-openwrt-postupgrade-check/scripts/collect-openwrt-postupgrade.sh

Return:
- status: healthy | degraded | blocked | unknown
- phase: router-ssh | release | package-restore | service-restore | storage | routing | k3s | steady-state
- blocking_issue: none or one short issue
- evidence: concise bullets with counts and object names
- next_check: when and what read-only check should run next
- recommended_action: what Codex/operator should consider; do not perform it yourself
```
