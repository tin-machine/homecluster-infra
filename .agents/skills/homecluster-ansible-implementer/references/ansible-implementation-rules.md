# Ansible Implementation Rules

This reference summarizes current `homecluster-infra` Ansible conventions. Use the nearest existing
role as the final source of style.

## Repository Boundaries

- Runtime inventory is supplied from outside this repository through the standard
  `../inventory.yml` contract. In the local operator workspace this file is generated from the
  private inventory repository next to `homecluster-infra`.
- Do not hand-edit generated `../inventory.yml` and call the task complete. Change the persistent
  source in `homecluster-inventory` when real inventory values must change.
- Do not add secrets, decrypted values, private keys, kubeconfig, tfvars, Terraform state, raw
  operation logs, real site CIDRs, or credentials to `homecluster-infra`.
- Public defaults may enable static validation, but must not become meaningful live desired state.
  Safe defaults are `enabled: false`, empty lists, empty strings with asserts, and no-op values.

## Variable Policy

- Use role-specific prefixes for public role variables:
  - OpenWrt roles: `openwrt_<role-or-domain>_*`
  - k3s roles: `k3s_<domain>_*`
  - shared Gentoo/PXE values keep the existing `openwrt_gentoo_*` names.
- Keep site-specific/live-impacting values in external inventory. Examples:
  - `openwrt_lan_ipaddr`
  - `openwrt_ula_prefix`
  - `openwrt_dhcp_ntp_servers`
  - `openwrt_gentoo_server_host`
  - `openwrt_frr_peer_listen_range`
  - `openwrt_syslog_remote_host`
  - `openwrt_storage_device`
  - `openwrt_storage_expected_model`
  - `openwrt_storage_expected_serial`
- Reject documentation address ranges for live endpoints:
  - `192.0.2.0/24`
  - `198.51.100.0/24`
  - `203.0.113.0/24`
- Avoid live endpoint fallback chains such as:

  ```yaml
  openwrt_gentoo_server_host | default(openwrt_lan_ipaddr)
  ```

  Make the external value explicit and assert when missing.

- Prefer role-entry normalization:

  ```yaml
  - name: 判定用変数を準備
    ansible.builtin.set_fact:
      example_enabled_resolved: "{{ example_enabled | default(false) | bool }}"
      example_path_resolved: "{{ example_path | default('', true) | string | trim }}"
  ```

  Then use the resolved facts in later tasks and templates.

## Role Defaults

- Put defaults in `roles/<role>/defaults/main.yml`.
- Defaults should make tagless/static checks parse, not silently configure a live site.
- For opt-in services, default to disabled:

  ```yaml
  openwrt_enable_prometheus_exporter: false
  openwrt_syslog_remote_enabled: false
  ```

- For required live input, use empty default plus early assert:

  ```yaml
  openwrt_syslog_remote_host: ""
  ```

- For destructive operations, use explicit booleans and confirmation variables:

  ```yaml
  openwrt_storage_force_repartition: false
  openwrt_storage_force_format: false
  openwrt_storage_destructive_confirm: ""
  openwrt_storage_expected_model: ""
  openwrt_storage_expected_serial: ""
  ```

## Task Style

- Use fully qualified module names, e.g. `ansible.builtin.assert`, `ansible.builtin.template`,
  `ansible.posix.mount`.
- Use Japanese task names when modifying Japanese-named roles/tasks; preserve existing file style.
- Use `assert` early for required inputs and type checks. Include an actionable `fail_msg`.
- Use `loop_control.label` for loops.
- Use `changed_when: false` for read-only commands and probes.
- Use `failed_when: false` only for intentional probes where the next task interprets the result.
- Use `check_mode: false` only when the command is intentionally needed during check mode; document
  the reason in the surrounding task structure if it is not obvious.
- For OpenWrt shell tasks, use `/bin/ash` when the target runs BusyBox ash:

  ```yaml
  args:
    executable: /bin/ash
  ```

- Prefer modules over shell when modules fit the target. Use shell when OpenWrt UCI/init behavior or
  BusyBox limitations make it clearer.
- For handlers that restart/reload services via command, mark actual reloads as changed:

  ```yaml
  changed_when: true
  ```

## Sensitive Data

Use `no_log: true` or an existing role toggle such as
`openwrt_pxe_client_catalog_no_log | default(true) | bool` for:

- secrets, tokens, hashes, private keys, netrc content, Slack tokens, Wi-Fi PSKs;
- rendered PXE ansible-pull group/client vars;
- host catalogs that include MACs, internal endpoints, or role vars;
- SSH authorized keys if they are generated from private inventory.

Do not print backup tarball contents, raw configs, credential files, SOPS material, kubeconfig, or
Terraform state.

## Tags And Gates

- Tagless OpenWrt apply must remain routine convergence only.
- Keep high-blast-radius operations behind `never` tags or an equivalent explicit gate:
  - `bootstrap_python`
  - `openwrt_storage`
  - `openwrt_gentoo_rootfs`
  - `openwrt_sysupgrade`
  - rootfs clone/prune/build
  - TFTP release switch
  - sysupgrade
  - storage partitioning/formatting
  - k3s runtime storage initialization/recreation
- For narrow subflows, use `include_role` with `tasks_from` and `apply.tags` as existing
  `ansible/openwrt/site.yml` does.
- Always inspect `--list-tasks` before live-running broad tags like `pxe`.

## Destructive Operation Pattern

For destructive or difficult-to-rollback changes:

1. Default all apply booleans to `false`.
2. Require a confirmation token derived from the target, e.g. `erase-<device>` or
   `<inventory_hostname> <target_version>`.
3. Verify target identity with model/serial/path when a block device is involved.
4. Reject protected mounts and ambiguous targets.
5. Show enough metadata for operator review without exposing secrets.
6. Document rollback/recovery and post-checks in runbook/docs.

Example structure:

```yaml
- name: destructive 操作の確認 token を検証
  ansible.builtin.assert:
    that:
      - example_confirm == example_confirm_expected
    fail_msg: >-
      destructive 操作には example_confirm={{ example_confirm_expected }} が必要です。
  when: example_apply | bool
```

## OpenWrt Patterns

- `ansible/openwrt/group_vars/openwrt.yml` is public-safe and must not define real live endpoints.
- OpenWrt SSH transfer currently uses SCP-oriented settings:

  ```yaml
  ansible_ssh_transfer_method: scp
  ansible_scp_extra_args: '-O'
  ansible_remote_tmp: /tmp
  ```

- Package install currently uses `ansible.builtin.opkg` or raw `opkg` for 24.10.x. Do not assume
  OpenWrt 25.x `apk` support unless that task explicitly asks for it.
- Validate live OpenWrt network inputs before templating `/etc/config/network`.
- `ansible_host` is the SSH target, not the LAN address to write into `/etc/config/network`.
- Before changing network/firewall/DHCP/FRR/syslog behavior, update or consult
  `docs/openwrt-live-apply-plan.md`.
- Treat `--check` as not read-only for OpenWrt. Some roles intentionally use command/shell probes
  and `check_mode: false`.

## k3s / ARM64 Patterns

- `ansible/arm64/site.yml` uses host intersections such as `env_stg:&role_k3s_stg_server`.
- Assert stage and role-specific invariants in `pre_tasks` before changing state.
- Use `become: true` for system files, systemd, kernel modules, sysctl, package changes, and
  mounts.
- Keep stage-local values in external inventory. Do not encode real node IPs, tokens, Wi-Fi secrets,
  storage IDs, kubeconfig, or Terraform site values in public defaults.
- For k3s staging server, require a single active server host before server-level operations.
- For observability/Terraform automation, require state mount paths and package pins before placing
  services or running package install paths.

## Documentation Updates

Update docs or runbooks when an implementation changes:

- live apply safety or blast radius;
- required external inventory keys;
- destructive confirmation tokens;
- backup/recovery/post-check procedure;
- role tags or operator command lines;
- known limitations or manual follow-up.

Use runbook wording that distinguishes:

- implemented behavior;
- plan-only behavior;
- manual fallback;
- unknowns that require live verification.

## Verification

Pick checks based on touched files:

```bash
bash scripts/ci/static-check.sh
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 \
  bash scripts/ci/static-check.sh
ansible-playbook -i examples/inventory.yml --syntax-check ansible/openwrt/site.yml
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --syntax-check
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --tags <tags> --list-hosts
ansible-playbook -i ../inventory.yml \
  ansible/openwrt/site.yml -l <host> --tags <tags> --list-tasks
```

For live acceptance, private operator workflow must additionally review:

- rendered external inventory;
- host/group limit resolution;
- documentation ranges or example selectors;
- exact commit SHA consumed by the target stage;
- saved plan/output without copying secrets into public logs.

Do not run actual live apply, sysupgrade, storage formatting, rootfs replacement, TFTP switch,
SwitchBot power actions, or Terraform apply unless explicitly requested.
