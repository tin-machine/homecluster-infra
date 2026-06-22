# Local LLM Review Checklist

Use this checklist when OpenCode or another smaller local model edits `homecluster-infra`.

## Before Editing

1. Confirm the branch with `git status --short --branch`.
2. Read the nearest existing files before editing. Do not replace a whole file unless the user asked
   for a rewrite.
3. If the user says a runbook or external directory is read-only, do not edit it. Report suggested
   wording in the final summary instead.
4. Do not override project permissions with broad inline permissions such as
   `OPENCODE_CONFIG_CONTENT='{"permission":{"bash":"allow","edit":"allow"}}'`. Broad inline
   permissions can bypass the project deny rules for real inventory and runbook edits.
5. In unattended OpenCode runs, prefer the read-only commands already allowed by project config:
   `git status --short --branch`, `git branch --show-current`, `git rev-parse`, `git diff`, `git show`,
   `git ls-files`, `rg`, `sed -n`, and the bundled review script.

## Preflight Gate

When a task uses a long plan file, an external runbook, or a prompt that may conflict with this
skill, run a preflight before implementation. The preflight phase is read-only and must not edit the
repository.

Use:

```bash
./.agents/skills/homecluster-ansible-implementer/scripts/opencode_preflight_gate.sh \
  --plan /path/to/plan.md \
  --task "short task description"
```

The preflight must answer these questions before implementation starts:

1. Are there conflicts between the skill and the task prompt?
2. Are there conflicts between the plan and the task prompt?
3. Are there conflicts between the plan and the skill?
4. Is user confirmation required before implementation?
5. May generation continue?

The output is a normalized JSON object. Proceed only when `may_proceed` is `true`,
`requires_user_confirmation` is `false`, and all conflict arrays are empty. If the preflight exits
nonzero, stop and report the blocking reason instead of continuing to edit files.

When the task prompt explicitly resolves plan-only or live-operation steps as source-only behavior,
the preflight should not keep them as conflicts. Examples of valid resolutions are "report proposed
runbook wording only", "do not execute live checks", "use example inventory only", and "convert live
command examples to public placeholders". Keep those decisions in `resolution_notes`.

The JSON must be internally consistent. If `may_proceed` is `true`, all conflict arrays must be
empty. If any conflict array is non-empty, `may_proceed` must be `false`.

## Public Boundary

Public files must not contain real operator paths, real host selectors, private repo URLs, secrets,
or raw logs.

Use these placeholders in public examples:

- inventory path: `../inventory.yml`
- host selector: `router.example`, `node.example`, or `<host>`
- site path: `/path/to/site-inputs` or `~/ghq/github.com/example-org/...`

Do not use:

- `/home/<real-user>/...`
- real router or node hostnames;
- RFC1918 addresses from the site;
- private token, key, kubeconfig, tfvars, state, backup tarball, or raw operation log content.

Plans and runbooks may contain live command examples for the operator. Do not copy those examples
verbatim into public README files. Convert them to placeholders before writing public docs.

## Defaults And Existing Behavior

When editing `roles/*/defaults/main.yml`, preserve existing keys unless the task explicitly asks to
remove them. After editing, compare with the original diff and check that unrelated defaults remain.

For `openwrt_sysupgrade`, these existing defaults must stay unless explicitly changed:

- `branch_type`
- `release_version`
- `preserve_settings`
- `perform_upgrade`
- `download_dir`
- `image_fs_type`
- `image_kind`
- `image_ext`
- `verify_sha256`
- `verify_md5`
- `base_url_releases`
- `base_url_snapshots`
- `wait_for_reconnect`
- `wait_for_reconnect_delay`
- `wait_for_reconnect_timeout`

For the backup fetch / confirmation guard feature, the new role-default keys are:

- `backup_fetch_enabled`
- `backup_dest`
- `sysupgrade_confirm`
- `sysupgrade_mode`
- `recovery_file_ready`
- `serial_or_usb_recovery_ready`
- `k3s_baseline_ready`

When adding defaults near the end of the file, preserve the anchor line you edit around. For
example, if inserting after `wait_for_reconnect_timeout`, the new text must include the original
`wait_for_reconnect_timeout` line plus the new defaults below it.

## Entrypoint Preservation

Do not rewrite `ansible/openwrt/site.yml` wholesale. It is the public OpenWrt/PXE baseline
entrypoint and must keep `hosts: openwrt`, `gather_facts: false`, `become: false`, the existing role
list, and the task-only include_role blocks for PXE maintenance. When adding role arguments, edit
only the `openwrt_sysupgrade` role block.

## Sysupgrade Flow

For `openwrt_sysupgrade`, preserve this ordering when adding backup fetch, recovery readiness, and
confirmation:

1. `verify.yml`
2. `backup.yml`
3. recovery readiness assert
4. confirmation token calculation/debug/assert
5. `upgrade.yml`

`backup.yml` must keep `owrt_backup_path`, fetch the tarball when enabled, and collect SHA256/size
metadata without printing backup contents.

The role README must document the public inventory-facing variables with placeholder examples:
`openwrt_sysupgrade_backup_fetch_enabled`, `openwrt_sysupgrade_backup_dest`, and
`openwrt_sysupgrade_confirm`, plus `openwrt_sysupgrade_mode` and the three recovery readiness
booleans. Use `router.example` or another placeholder host selector, not a live site hostname.

`detect` must remain the non-mutating default. `prepare` may verify/download and create/fetch the
backup but must not import `upgrade.yml`. `upgrade` must require recovery readiness and confirmation
before importing `upgrade.yml`.

## Runbook Wording

Use state wording precisely:

- plan-only: "planned" or "must be implemented";
- branch under review: "implemented on the branch and under source validation";
- merged and validated: "implemented in main as of <date>";
- live verified: "verified against the live target on <date>".

Do not write "implemented" in runbooks merely because a local model produced a diff.

## Required Self Review

Before final response, run:

```bash
git diff --check
./.agents/skills/homecluster-ansible-implementer/scripts/review_changed_ansible.sh
```

If either command fails, fix the reported lines and rerun. Do not report static checks as passed
until the rerun succeeds.

Use the script path exactly as shown. Do not read or execute `/.agents/...`; that is an absolute path
outside the repository and is wrong.

Any nonzero exit from the review script means validation failed. Do not call it "minor" or
"successful", and do not replace it with ad hoc syntax checks. Fix the reported failures, rerun the
same script, and only then summarize verification as passed.

When editing Markdown, blank separator lines must be empty. Do not insert lines that contain only a
space. Keep one final newline at end of file, not an extra blank line.

## Live Inventory Commands

Do not run Ansible against real operator inventory during local LLM implementation, even for
detect-only checks. Use `examples/inventory.yml` for syntax validation. If a live detect-only check
seems useful, report the exact command as a proposal and wait for operator approval.

Allowed during local LLM implementation:

```bash
ansible-playbook --syntax-check ansible/openwrt/site.yml -i examples/inventory.yml
ansible-playbook --syntax-check ansible/openwrt/playbooks/upgrade-openwrt.yml -i examples/inventory.yml
```

Do not run syntax checks without `-i examples/inventory.yml`; they can pass against implicit
localhost while skipping the intended host pattern, which is weak evidence.

Not allowed without explicit operator approval:

```bash
ansible-playbook -i ../inventory.yml ...
ansible-playbook -i /path/to/generated/inventory.yml ...
```

The same restriction applies to detect-only runs. `openwrt_perform_upgrade=false` avoids backup and
sysupgrade, but it can still connect to the live router and run probes.

## Runbook Dirty Check

For implementation experiments, tracked files under the sibling runbook repository's
`docs/operation/` directory should stay clean. If a runbook should change, report the proposed
wording instead of editing it. The review script checks tracked modifications by default. Set
`SKIP_RUNBOOK_DIRTY_CHECK=1` only when the user explicitly asked to edit runbooks.
