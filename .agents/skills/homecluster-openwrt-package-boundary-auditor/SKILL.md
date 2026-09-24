---
name: homecluster-openwrt-package-boundary-auditor
description: Audit OpenWrt package-manager source boundaries in tin-machine/homecluster-infra without touching live routers. Use when Codex or OpenCode is asked to verify that direct opkg/apk usage is limited to approved roles, that openwrt_package include tasks keep the expected task-level vars contract, or that OpenWrt 25.x package-manager source prep has not regressed.
---

# Homecluster OpenWrt Package Boundary Auditor

## Scope

Use this skill for source-only OpenWrt package-manager regression checks in `homecluster-infra`.

This skill is read-only. Do not run Ansible against real inventory, SSH to routers, run sysupgrade,
install packages, restart services, reboot devices, use SwitchBot, run Terraform apply, or inspect
secrets.

## Workflow

1. Read `docs/ai-context.md` only as needed for public repository boundaries.
2. Run the package boundary checker:

    ```bash
    ./.agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py
    ```

3. Treat the checker JSON as authoritative. `ok: true` means no blocking source-boundary
    regression was found. `unexpected_package_manager_calls` lists direct `opkg` / `apk` usage
    outside approved boundary roles. `include_role_issues` lists malformed `openwrt_package`
    include tasks.
4. If `ok` is false, report the exact file, line, role, and issue. Do not repair unless the task
    explicitly asks for implementation.

## Approved Boundaries

Direct package-manager commands or modules are expected only in:

- `openwrt_package`: the common package install/remove role.
- `bootstrap_python`: raw pre-Python bootstrap path.
- `openwrt_detect`: raw package-manager probe.
- `openwrt_sysupgrade`: manifest and package-manager probe/reporting path.

Every other OpenWrt role should use `ansible.builtin.include_role` with `name: openwrt_package`
for package install/remove tasks.

## OpenCode Contract

For OpenCode/local LLM delegation, use the repository agent
`homecluster-package-boundary-auditor` or another read-only validation agent. The task should ask
OpenCode to run the checker exactly once and return the compact JSON status. OpenCode must not edit
files, run broad validation, or touch live state.

Use the template in `references/opencode-task-prompt.md`.
