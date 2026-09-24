---
name: homecluster-openwrt-major-upgrade
description: Prepare OpenWrt major-version Ansible changes in tin-machine/homecluster-infra, especially 24.10 opkg to 25.x apk package-manager migration, openwrt_detect/openwrt_package roles, bootstrap_python raw package-manager branches, sysupgrade post-checks, and local-LLM/OpenCode implementation guardrails. Use when Codex or OpenCode is asked to implement, review, or plan OpenWrt 25.x/major-upgrade support without running live apply or sysupgrade.
---

# Homecluster OpenWrt Major Upgrade

## Workflow

Use this skill for OpenWrt major-version preparation work in `homecluster-infra`.

1. Follow `homecluster-ansible-implementer` safety rules and repository docs first.
2. Read
   `.agents/skills/homecluster-openwrt-major-upgrade/references/major-upgrade-implementation-rules.md`
   before editing Ansible code. Resolve this path from the repository root when running under
   OpenCode.
3. Keep each implementation pass narrow. Prefer this order:
   - add `openwrt_detect`;
   - add `openwrt_package`;
   - convert one low-risk role to `openwrt_package`;
   - add `bootstrap_python` raw `apk` branch;
   - update sysupgrade post-checks and collectors.
4. Do not run live apply, real inventory, sysupgrade, SwitchBot, Terraform apply, destructive
   storage operations, or secret inspection from this skill.
5. Use examples inventory, static checks, syntax checks, list-tasks, and review scripts only.

## OpenCode Contract

When delegating to OpenCode/local LLM, give exactly one implementation unit and require it to
report:

- changed files;
- validation commands it ran;
- any skipped validation and why;
- any uncertainty about OpenWrt `apk` semantics.

If OpenCode broadens the task, touches real inventory, or tries live commands, stop and tighten the
prompt, skill, or `opencode.json` before continuing.
