# OpenCode Task Prompt Template

Use this template when delegating the package-manager boundary regression scan to OpenCode/local
LLM.

```text
Use $homecluster-openwrt-package-boundary-auditor to run a source-only OpenWrt package-manager
boundary audit.

Scope:
- Read-only source audit only.
- Run the checker exactly once.
- Do not edit files.
- Do not run Ansible against real inventory.
- Do not SSH to routers or k3s nodes.
- Do not run sysupgrade, package install, service restart, reboot, SwitchBot, Terraform apply, or
  secret-inspecting commands.

Checker command:

./.agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py

Return:
- ok: true | false
- unexpected_package_manager_calls count
- include_role_issues count
- one short blocker summary if ok is false
```
