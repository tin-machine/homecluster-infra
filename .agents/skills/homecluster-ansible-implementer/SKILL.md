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

Prefer these checks after implementation, adjusted to the changed entrypoint:

```bash
bash scripts/ci/static-check.sh
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 \
  bash scripts/ci/static-check.sh
./.agents/skills/homecluster-ansible-implementer/scripts/review_changed_ansible.sh
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --syntax-check
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --tags <tags> --list-tasks
```

Do not run live apply, destructive gates, sysupgrade, storage formatting, rootfs replacement, TFTP
switching, k3s node rebuild, Terraform apply, or SwitchBot power actions without explicit operator
approval.
