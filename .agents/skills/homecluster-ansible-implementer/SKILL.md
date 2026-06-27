---
name: homecluster-ansible-implementer
description: Implement or modify Ansible code in tin-machine/homecluster-infra. Use when Codex is asked to add or change Ansible roles, tasks, handlers, defaults, playbooks, inventory-facing variables, OpenWrt/PXE/Gentoo/k3s staging automation, destructive-operation guards, or validation for this repository while preserving its public/private boundary and live-apply safety rules.
---

# Homecluster Ansible Implementer

## Workflow

Use this skill for Ansible implementation work in `homecluster-infra`.

1. Follow the current AGENTS.md instructions first. For `homecluster-*` docs and planning, search
   DevRag before forming a plan; fall back to local-doc-search or `rg` when needed.
2. Before editing implementation files, create and switch to a dedicated Git branch with a
   descriptive name such as `codex/<task-topic>`. If a suitable task branch already exists, confirm
   the current branch and continue there.
3. Read the relevant repository docs before editing:
   - `docs/ai-context.md`
   - `docs/site-input-contract.md`
   - `docs/architecture-decision-record/0010-inventory-boundary.md`
   - `docs/openwrt-live-apply-plan.md` for OpenWrt live-impacting changes
   - `docs/full-execution-validation.md` for verification expectations
4. Read [references/ansible-implementation-rules.md](references/ansible-implementation-rules.md)
   before making changes. It contains the local Ansible conventions and guardrails.
5. For local LLM or OpenCode implementation runs, also read
   [references/local-llm-review-checklist.md](references/local-llm-review-checklist.md). It is a
   low-freedom checklist for the failure modes seen in this repository.
6. For long external plans or prompts that may conflict with this skill, run the preflight gate
   before implementation:
   `./.agents/skills/homecluster-ansible-implementer/scripts/opencode_preflight_gate.sh`.
7. Inspect the nearest existing role/playbook and copy its style before adding new structure.
8. Keep changes scoped. Update docs or runbooks when the behavior, operator gate, or verification
   path changes.
9. Verify with syntax/static checks appropriate to the changed entrypoints. Treat `--check` as
   potentially state-touching, not as a read-only proof.

## Design Priorities

- Preserve the public/private boundary: real inventory, secrets, state, kubeconfig, tfvars, and raw
  operation logs stay outside this repository.
- Fail closed for live-impacting site values. Do not add meaningful public defaults for IPs,
  subnets, routes, storage devices, BGP policy, syslog destinations, PXE/NFS/TFTP endpoints, or
  recovery-only SSH policy.
- Keep tagless Ansible for routine convergence only. Destructive or high-blast-radius operations
  must sit behind `never` tags or equivalent explicit gates.
- Prefer role-local effective facts plus early asserts over fallback chains scattered through tasks
  and templates.
- Use `no_log` for rendered runtime variables, credentials, SSH keys, tokens, Wi-Fi secrets, PXE
  ansible-pull vars, and host-specific catalogs that can expose site-sensitive data.

## Verification Baseline

When Codex delegates implementation to OpenCode/local LLM, run through the wrapper instead of
calling `opencode run` directly. The wrapper rejects output-limit truncation, zero-diff
implementation attempts, repeated tool-error loops, denied tool attempts, and failed validation.
Tool permission enforcement belongs in
`opencode.json`:

```bash
./.agents/skills/homecluster-ansible-implementer/scripts/opencode_implementation_run.sh \
  --model local-gemma4/gemma-4-12b-it-qat-q4_0.gguf \
  --config ~/.config/opencode/local-gemma4.json \
  --edit-only \
  --task "<one narrow implementation task>"
```

The wrapper default agent is `homecluster-source-edit`. For tasks that need Ansible/project skill
context, pass `--agent homecluster-ansible-patch` explicitly.

For local Gemma4, prefer edit-only runs first. Codex then runs
`opencode_validation_gate.sh` and saves its compact JSON. If validation fails, start a second repair
run with only that compact validation JSON. Use the same agent boundary as the original run:

```bash
./.agents/skills/homecluster-ansible-implementer/scripts/opencode_implementation_run.sh \
  --model local-gemma4/gemma-4-12b-it-qat-q4_0.gguf \
  --config ~/.config/opencode/local-gemma4.json \
  --agent homecluster-ansible-patch \
  --edit-only \
  --repair-json /tmp/opencode-validation.json \
  --task "<same narrow implementation task>"
```

Choose the OpenCode agent by privilege boundary:

- `homecluster-read-only`: scout candidate files and anchors before implementation.
- `homecluster-edit-only`: apply one exact `oldString` / `newString` replacement.
- `homecluster-source-edit`: read current source and apply one narrow edit without commands,
  validation, repair, or skill access. Use this for normal source edits that need current-file reads.
- `homecluster-ansible-patch`: implement one narrow source-only Ansible patch when the task needs
  Ansible/project skill context.
- `homecluster-validation-runner`: run approved validation commands and report compact results.
- `homecluster-repair-only`: repair one compact validation failure against the current target file.

Prefer these checks after implementation, adjusted to the changed entrypoint:

```bash
./.agents/skills/homecluster-ansible-implementer/scripts/opencode_validation_gate.sh
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --syntax-check
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --tags <tags> --list-tasks
```

Do not run live apply, destructive gates, sysupgrade, storage formatting, rootfs replacement, TFTP
switching, k3s node rebuild, Terraform apply, or SwitchBot power actions without explicit operator
approval.
