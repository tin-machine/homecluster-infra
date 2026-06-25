# OpenWrt Major Upgrade Implementation Rules

## Scope

This reference covers public-safe Ansible source changes for preparing OpenWrt 25.x or later.
It does not authorize live upgrade, live apply, real inventory execution, package installation on a
router, sysupgrade, SwitchBot power actions, Terraform apply, or secret access.

## Design Rules

- Preserve OpenWrt 24.10.x behavior first. The first acceptance target is that the `opkg` path works
  exactly as existing roles did.
- Detect package manager from commands on the target, not only from version strings.
- Prefer facts:
  - `openwrt_pkg_manager`: `opkg` or `apk`;
  - `openwrt_pkg_manager_command`: detected command path or command name;
  - `openwrt_release_major`: first numeric major component when available.
- Fail closed if no supported package manager exists.
- Warn or fail on inconsistent states, for example 25.x release with only `opkg`, or 24.x release
  with only `apk`. In examples/static checks, keep this parseable without real hosts.
- Do not assume `community.general.apk` works for OpenWrt. Treat OpenWrt `apk` as unverified until
  proven on a test image or recovery-safe device.
- Keep `bootstrap_python` raw-command based. It must work before Python is available on OpenWrt.
- Keep destructive or high-blast-radius operations behind existing `never` tags or explicit
  confirmation gates.

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
