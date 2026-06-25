# OpenCode Task Prompt Template

Use this shape for local LLM implementation runs:

```text
Use $homecluster-openwrt-major-upgrade for a narrow OpenWrt major-upgrade preparation task.

Task:
- Current branch: <branch>.
- Implement only <one concrete unit>.
- Preserve OpenWrt 24.10.x opkg behavior.
- Do not run live apply, real inventory, sysupgrade, SwitchBot, Terraform apply, destructive
  storage operations, or secret-inspecting commands.
- Use examples inventory or static checks only.
- Return changed files, validation run, skipped validation, and uncertainty.

Required reads:
- .agents/skills/homecluster-openwrt-major-upgrade/SKILL.md
- .agents/skills/homecluster-openwrt-major-upgrade/references/major-upgrade-implementation-rules.md
- .agents/skills/homecluster-ansible-implementer/references/ansible-implementation-rules.md
```
