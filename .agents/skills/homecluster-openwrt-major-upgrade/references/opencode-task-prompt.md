# OpenCode Task Prompt Template

Use this shape for local LLM implementation runs:

```text
Use $homecluster-openwrt-major-upgrade for a narrow OpenWrt major-upgrade preparation task.

Task kind: openwrt_package_role_conversion
Current branch: <branch>
Editable file: <one file>
Target task name: <exact task name>
Package expression: <existing list variable or expression>
Preserve:
- OpenWrt 24.10.x opkg behavior through openwrt_package.
- Existing task name and existing when condition, if any.
- All unrelated tasks, defaults, handlers, templates, and docs.
Remove:
- ansible.builtin.opkg from the target package task.
- loop and loop_control from the target package task.
Forbidden:
- Live apply, real inventory, sysupgrade, SwitchBot, Terraform apply, destructive storage
  operations, or secret-inspecting commands.
- Validation commands during the edit-only run.
Search discipline:
- If Semble MCP is available, call Semble search exactly once before broad grep/rg.
- Use a query containing the expected role/file/script name plus specific identifiers.
- If Semble does not return the expected subsystem, stop with no_confident_location.
- If Semble returns the expected subsystem, read only that file around the returned line.
Stop after edit: yes.
Final output: changed files and uncertainty only.

Repair run:
- Read only the compact validation JSON passed by --repair-json.
- Re-read the current editable file from disk before editing.
- Make the smallest source edit that fixes the compact validation failure.
- Do not use an oldString captured before the failed validation.

Required reads:
- .agents/skills/homecluster-openwrt-major-upgrade/SKILL.md
- .agents/skills/homecluster-openwrt-major-upgrade/references/major-upgrade-implementation-rules.md
- .agents/skills/homecluster-ansible-implementer/references/ansible-implementation-rules.md
```
