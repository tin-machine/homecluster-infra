# OpenWrt Major Upgrade Implementation Rules

## Scope

This reference covers public-safe Ansible source changes for preparing OpenWrt 25.x or later.
It does not authorize live upgrade, live apply, real inventory execution, package installation on a
router, sysupgrade, SwitchBot power actions, Terraform apply, or secret access.
Adding or reviewing Ansible source files that would install packages during a future approved playbook
run is allowed in this source-only workflow; actually running those tasks against a router is not.

## Design Rules

- Preserve OpenWrt 24.10.x behavior first. The first acceptance target is that the `opkg` path works
  exactly as existing roles did.
- Detect package manager from commands on the target, not only from version strings.
- Prefer facts:
  - `openwrt_pkg_managers_available`: detected supported managers such as `['opkg']`,
    `['apk']`, or `['apk', 'opkg']`;
  - `openwrt_pkg_manager_preferred`: the version-aware manager selected for current tasks;
  - `openwrt_pkg_manager`: compatibility alias for `openwrt_pkg_manager_preferred`;
  - `openwrt_pkg_manager_command`: detected command path or command name;
  - `openwrt_release_major`: first numeric major component when available.
- Fail closed if no supported package manager exists.
- Warn or fail on inconsistent states, for example 25.x release with only `opkg`, or 24.x release
  with only `apk`. In examples/static checks, keep this parseable without real hosts.
- Do not weaken the consistency check so that it only runs when both `apk` and `opkg` are present.
  Single-manager mismatches such as OpenWrt 24.x with only `apk` or OpenWrt 25.x with only `opkg`
  must still fail closed.
- When both `apk` and `opkg` are present, select the package manager by release major, not by probe
  order: OpenWrt 24.x uses `opkg`; OpenWrt 25.x and later uses `apk`. This preserves the coexistence
  period where both commands may be installed.
- Do not assume `community.general.apk` works for OpenWrt. Treat OpenWrt `apk` as unverified until
  proven on a test image or recovery-safe device.
- Keep `bootstrap_python` raw-command based. It must work before Python is available on OpenWrt.
- Keep destructive or high-blast-radius operations behind existing `never` tags or explicit
  confirmation gates.

## OpenCode Search Discipline

When Semble MCP is available in OpenCode, use it as a first-pass search gate, not as final proof.
This reduces broad repository grep/read passes, but only when the query is specific.

For exploratory implementation tasks:

1. Call Semble search exactly once before broad `grep`/`rg`.
2. Include the expected role, file, collector, or script name plus the most specific identifiers in
   the query. For example, prefer `collect-openwrt-postupgrade packages_raw opkg apk` over
   `OpenWrt package manager`.
3. If the top results are outside the expected subsystem, stop and report `no_confident_location`
   instead of expanding into repository-wide grep.
4. If the top result is in the expected subsystem, read only that file around the returned line and
   confirm the exact implementation line before editing.
5. Use repository-wide `grep`/`rg` only when the task needs every literal occurrence, or when Semble
   returns no plausible subsystem result.

Semble line numbers can land near the relevant chunk rather than on the exact statement. Treat them
as navigation hints. The exact edit location still requires reading the returned file.

## Implementation Order

### 1. `openwrt_detect`

Add a role that can run early and read:

- `/etc/openwrt_release`;
- `ubus call system board` when available;
- `command -v apk`;
- `command -v opkg`.

Use `ansible.builtin.raw` for remote probes in this role. Do not use `slurp`, `command`, or
`shell` here: `openwrt_detect` is wired before `bootstrap_python`, so the target may not have Python
yet. Set facts after raw probes. Do not install packages.

### 2. `openwrt_package`

Add a shared package task that accepts:

- `openwrt_package_names`;
- `openwrt_package_state`, default `present`;
- `openwrt_package_update_cache`, default from `openwrt_opkg_update | default(true)`.

For `opkg`, preserve existing behavior with the current module or equivalent command. For `apk`,
prefer a small raw-command path until OpenWrt `apk` module compatibility is verified.

### 3. Role Conversions

Convert one role at a time. Start with low-risk package-only roles before roles that remove packages
or mutate service state.

Prefer the converter script for package task rewrites before hand-editing YAML:

```bash
scripts/ansible/convert_openwrt_package_task.py \
  --file ansible/openwrt/roles/<role>/tasks/main.yml \
  --task-name "<exact task name>" \
  --package-expression openwrt_example_packages \
  --state present
```

The default mode is dry-run and prints a unified diff. Add `--write` only after the diff matches the
intended task. For simple removal tasks such as raw `opkg remove dnsmasq`, use `--state absent`;
the script can derive a literal package list from `opkg remove <package>`.

Under OpenCode, run the converter command directly with the known arguments from the task prompt.
Do not spend an implementation pass on `--help`; project permissions intentionally allow only the
converter path, not broad Python execution.
Put the exact converter command in a fenced `bash` block without trailing sentence punctuation. Once
the converter exits 0 and `git diff -- <target-file>` shows the expected task-only diff, stop the
edit-only run; do not read the full converter script, the full large task file, or unrelated role
directories.

When passing an existing package-list variable into `openwrt_package`, render the variable value with
Jinja. A bare variable name becomes a string literal in Ansible and will not pass a list value.

Use:

```yaml
- name: Example packages を導入
  ansible.builtin.include_role:
    name: openwrt_package
  vars:
    openwrt_package_names: "{{ openwrt_example_packages }}"
    openwrt_package_state: present
    openwrt_package_update_cache: "{{ openwrt_opkg_update | default(true) }}"
```

`vars:` belongs to the task, at the same indentation as `ansible.builtin.include_role` and `when`.
Do not place `openwrt_package_names` or `vars` under `ansible.builtin.include_role`; those become
invalid module options.

Do not use:

```yaml
vars:
  openwrt_package_names: openwrt_example_packages
```

Candidate order:

1. `openwrt_backup_share`
2. `openwrt_nfs_server`
3. `openwrt_prometheus_exporter`
4. `openwrt_frr`
5. `openwrt_banip`
6. `openwrt_storage`
7. `openwrt_pxe_dnsmasq`
8. `openwrt_gentoo_rootfs`

### 4. `bootstrap_python`

Add raw `apk` probes/install only after `openwrt_detect` and `openwrt_package` have stable naming.
Keep the existing Python symlink and zlib checks.

This role is a pre-Python bootstrap path. Keep package-manager operations in `ansible.builtin.raw`;
do not switch the probes/install tasks to `command`, `shell`, `opkg`, `apk`, or `openwrt_package`
modules unless a future task proves those modules work before Python exists on the target.

For OpenCode/local Gemma4 runs that touch only this role, keep the investigation bounded:

- read `ansible/openwrt/roles/bootstrap_python/tasks/main.yml` and the role defaults only if needed;
- do not run repository-wide `grep`/`rg` for package-manager variables unless the task explicitly
  asks for cross-role changes;
- do not emit a long planned patch in the response before editing;
- edit first, then stop and let Codex run the validation gate.

The coexistence rule still applies inside this raw bootstrap path: preserve the `opkg` behavior for
OpenWrt 24.x, add an `apk` path for OpenWrt 25.x or detected `apk`, and fail closed when neither
package manager can be selected.

### 5. Sysupgrade And Collectors

After package-manager abstraction exists, update sysupgrade/post-upgrade checks to record package
manager before and after upgrade. Do not mix this with executing a major sysupgrade.

## Validation

For every pass, run at least:

```bash
git diff --check
bash scripts/ci/static-check.sh
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 bash scripts/ci/static-check.sh
./.agents/skills/homecluster-ansible-implementer/scripts/review_changed_ansible.sh
```

For OpenWrt site entrypoint changes, also run:

```bash
ansible-playbook -i examples/inventory.yml ansible/openwrt/site.yml --list-tasks
```

Use `examples/inventory.yml` only. Do not use `../inventory.yml`, homecluster-inventory, or any
absolute private inventory path from OpenCode.
