# Local LLM Review Checklist

Use this checklist when OpenCode or another smaller local model edits `homecluster-infra`.

## Before Editing

1. Confirm the branch with `git status --short --branch`.
2. Read the nearest existing files before editing. Do not replace a whole file unless the user asked
   for a rewrite.
3. If the user says a runbook or external directory is read-only, do not edit it. Report suggested
   wording in the final summary instead.

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
