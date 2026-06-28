#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
failures=0

record_failure() {
  failures=$((failures + 1))
  echo "FAILED: $*" >&2
}

echo "== skill review: diff whitespace =="
if ! git diff --check; then
  record_failure "git diff --check reported whitespace or patch formatting issues"
fi

echo
echo "== skill review: changed files =="
base_ref="${HOMECLUSTER_REVIEW_BASE_REF:-origin/main}"
base_commit=""
if git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  base_commit="$(git merge-base "$base_ref" HEAD)"
fi

mapfile -t changed_files < <(
  {
    if [[ -n "$base_commit" ]]; then
      git diff --name-only --diff-filter=ACMRT "${base_commit}..HEAD" --
    fi
    git diff --name-only --diff-filter=ACMRT HEAD --
    git ls-files --others --exclude-standard
  } | sort -u
)
if ((${#changed_files[@]} == 0)); then
  echo "no changed files"
else
  printf '%s\n' "${changed_files[@]}"
fi

echo
echo "== skill review: public redaction patterns =="
public_files=()
for path in "${changed_files[@]}"; do
  case "$path" in
    *.md|README|README.*|docs/*|opencode.json)
      if [[ -f "$path" ]]; then
        public_files+=("$path")
      fi
      ;;
  esac
done

if ((${#public_files[@]} > 0)); then
  redaction_patterns=(
    "/home/[A-Za-z0-9._-]+"
    "10\\.[0-9]+\\.[0-9]+\\.[0-9]+"
    "192\\.168\\."
    "home""-router"
    "k3s""-prd"
    "pico""claw"
    "soft""ether"
  )
  for pattern in "${redaction_patterns[@]}"; do
    if rg -n "$pattern" "${public_files[@]}"; then
      record_failure "public redaction pattern found in changed files"
    fi
  done
else
  echo "no changed public docs/config files"
fi

echo
echo "== skill review: markdownlint changed markdown =="
markdownlint_targets=()
for path in "${changed_files[@]}"; do
  case "$path" in
    *.md|README|README.*)
      if [[ -f "$path" ]]; then
        markdownlint_targets+=("$path")
      fi
      ;;
  esac
done
if ((${#markdownlint_targets[@]} > 0)) && command -v markdownlint >/dev/null 2>&1; then
  if ! markdownlint --disable MD013 -- "${markdownlint_targets[@]}"; then
    record_failure "markdownlint failed for changed markdown"
  fi
else
  echo "OK: skipped; no changed markdown files or markdownlint unavailable"
fi
if command -v python3 >/dev/null 2>&1; then
  if ! python3 scripts/ci/check-changed-markdown-style.py "${markdownlint_targets[@]}"; then
    record_failure "changed markdown style check failed"
  fi
else
  echo "OK: skipped changed markdown style check; python3 unavailable"
fi

echo
echo "== skill review: openwrt_sysupgrade defaults key preservation =="
defaults_file="ansible/openwrt/roles/openwrt_sysupgrade/defaults/main.yml"
if [[ -f "$defaults_file" ]]; then
  required_keys=(
    branch_type
    release_version
    preserve_settings
    perform_upgrade
    download_dir
    image_fs_type
    image_kind
    image_ext
    verify_sha256
    verify_md5
    base_url_releases
    base_url_snapshots
    wait_for_reconnect
    wait_for_reconnect_delay
    wait_for_reconnect_timeout
    backup_fetch_enabled
    backup_dest
    sysupgrade_confirm
    sysupgrade_mode
    recovery_file_ready
    serial_or_usb_recovery_ready
    k3s_baseline_ready
  )
  for key in "${required_keys[@]}"; do
    if ! rg -q "^${key}:" "$defaults_file"; then
      record_failure "missing required existing default key: ${key}"
    fi
  done
  if ((failures == 0)); then
    echo "openwrt_sysupgrade defaults keys present"
  else
    echo "openwrt_sysupgrade defaults key check completed with failures"
  fi
fi

echo
echo "== skill review: openwrt_sysupgrade README public variables =="
sysupgrade_readme="ansible/openwrt/roles/openwrt_sysupgrade/README.md"
if [[ -f "$sysupgrade_readme" ]]; then
  readme_required_patterns=(
    "openwrt_sysupgrade_backup_fetch_enabled"
    "openwrt_sysupgrade_backup_dest"
    "openwrt_sysupgrade_confirm"
    "openwrt_sysupgrade_mode"
    "openwrt_sysupgrade_recovery_file_ready"
    "openwrt_sysupgrade_serial_or_usb_recovery_ready"
    "openwrt_sysupgrade_k3s_baseline_ready"
    "detect"
    "prepare"
    "upgrade"
    "router.example"
    "SHA256"
  )
  for pattern in "${readme_required_patterns[@]}"; do
    if ! rg -q "$pattern" "$sysupgrade_readme"; then
      record_failure "openwrt_sysupgrade README lost expected public documentation pattern: ${pattern}"
    fi
  done
fi

echo
echo "== skill review: OpenWrt site entrypoint preservation =="
site_file="ansible/openwrt/site.yml"
if [[ -f "$site_file" ]]; then
  site_required_patterns=(
    "^- name: OpenWrt PXE infrastructure$"
    "^  hosts: openwrt$"
    "^  gather_facts: false$"
    "^  become: false$"
    "^    - role: bootstrap_python$"
    "^    - role: openwrt_storage$"
    "^    - role: openwrt_pxe_client_catalog$"
    "^    - role: openwrt_gentoo_rootfs$"
    "^    - role: openwrt_backup_share$"
    "^    - role: openwrt_nfs_server$"
    "^    - role: openwrt_network$"
    "^    - role: openwrt_ntp$"
    "^    - role: openwrt_firewall$"
    "^    - role: openwrt_banip$"
    "^    - role: openwrt_dhcp$"
    "^    - role: openwrt_pxe_dnsmasq$"
    "^    - role: openwrt_wireless$"
    "^    - role: openwrt_sysupgrade$"
    "^    - role: openwrt_frr$"
    "^    - role: openwrt_prometheus_exporter$"
    "^    - role: openwrt_syslog_remote$"
    "^  tasks:$"
    "tasks_from: tftp_switch"
    "tasks_from: networkd_config"
    "tasks_from: rootfs_clone"
    "tasks_from: rootfs_prune"
  )
  for pattern in "${site_required_patterns[@]}"; do
    if ! rg -q "$pattern" "$site_file"; then
      record_failure "OpenWrt site entrypoint lost expected pattern: ${pattern}"
    fi
  done
fi

echo
echo "== skill review: openwrt_detect package-manager coexistence facts =="
detect_tasks_file="ansible/openwrt/roles/openwrt_detect/tasks/main.yml"
package_tasks_file="ansible/openwrt/roles/openwrt_package/tasks/main.yml"
if [[ -f "$detect_tasks_file" ]]; then
  detect_required_patterns=(
    "openwrt_pkg_managers_available:"
    "openwrt_pkg_manager_preferred:"
    "openwrt_pkg_manager_preferred \\| string \\| trim"
    "openwrt_detect_apk.rc == 0"
    "openwrt_detect_opkg.rc == 0"
    "openwrt_release_major.*>= 25"
    "openwrt_release_major.*< 25"
  )
  for pattern in "${detect_required_patterns[@]}"; do
    if ! rg -q "$pattern" "$detect_tasks_file"; then
      record_failure "openwrt_detect lost expected package-manager coexistence pattern: ${pattern}"
    fi
  done

  if rg -n "openwrt_pkg_managers_available[[:space:]]*\\|[[:space:]]*length[[:space:]]*==[[:space:]]*2" "$detect_tasks_file"; then
    record_failure "openwrt_detect consistency asserts must not only fail when both package managers are present"
  fi

  if ! rg -q ">= 25 and openwrt_pkg_manager != 'apk'" "$detect_tasks_file"; then
    record_failure "openwrt_detect must fail closed for OpenWrt 25.x+ unless apk is selected"
  fi
  if ! rg -q "> 0 and .*< 25 and openwrt_pkg_manager != 'opkg'" "$detect_tasks_file"; then
    record_failure "openwrt_detect must fail closed for OpenWrt 1.x-24.x unless opkg is selected"
  fi

  python3 - <<'PY'
cases = [
    ("24", ("opkg",), "opkg", True),
    ("24", ("apk",), "apk", False),
    ("24", ("apk", "opkg"), "opkg", True),
    ("25", ("apk",), "apk", True),
    ("25", ("opkg",), "opkg", False),
    ("25", ("apk", "opkg"), "apk", True),
    ("", ("opkg",), "opkg", True),
    ("", ("apk",), "apk", True),
    ("", (), "", False),
]

def expected_ok(release_major, managers, selected):
    major = int(release_major or "0")
    if not managers or selected not in ("opkg", "apk"):
        return False
    if major >= 25 and selected != "apk":
        return False
    if 0 < major < 25 and selected != "opkg":
        return False
    return True

for release_major, managers, selected, expected in cases:
    actual = expected_ok(release_major, managers, selected)
    if actual != expected:
        raise SystemExit(
            "package-manager truth table mismatch: "
            f"release_major={release_major!r} managers={managers!r} "
            f"selected={selected!r} expected={expected!r} actual={actual!r}"
        )

print("package-manager fail-closed truth table ok")
PY
fi
if [[ -f "$package_tasks_file" ]]; then
  package_required_patterns=(
    "openwrt_package_manager_effective:"
    "openwrt_pkg_manager_preferred"
    "default\\(openwrt_pkg_manager"
  )
  for pattern in "${package_required_patterns[@]}"; do
    if ! rg -q "$pattern" "$package_tasks_file"; then
      record_failure "openwrt_package lost expected preferred-manager fallback pattern: ${pattern}"
    fi
  done
fi

echo
echo "== skill review: openwrt_sysupgrade backup and confirmation flow =="
backup_tasks_file="ansible/openwrt/roles/openwrt_sysupgrade/tasks/backup.yml"
main_tasks_file="ansible/openwrt/roles/openwrt_sysupgrade/tasks/main.yml"
if [[ -f "$backup_tasks_file" && -f "$main_tasks_file" ]]; then
  flow_required_patterns=(
    "owrt_backup_path:"
    "ansible.builtin.fetch:"
    "checksum_algorithm: sha256"
    "fetched_backup_sha256:"
    "owrt_sysupgrade_mode:"
    "owrt_sysupgrade_mode in \\[\"detect\", \"prepare\", \"upgrade\"\\]"
    "owrt_sysupgrade_confirm_expected:"
    "recovery_file_ready | bool"
    "serial_or_usb_recovery_ready | bool"
    "k3s_baseline_ready | bool"
    "ansible.builtin.assert:"
    "ansible.builtin.import_tasks: upgrade.yml"
  )
  for pattern in "${flow_required_patterns[@]}"; do
    if ! rg -q "$pattern" "$backup_tasks_file" "$main_tasks_file"; then
      record_failure "openwrt_sysupgrade flow lost expected pattern: ${pattern}"
    fi
  done

  verify_import_line="$(rg -n "ansible.builtin.import_tasks: verify.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
  backup_import_line="$(rg -n "ansible.builtin.import_tasks: backup.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
  recovery_assert_line="$(rg -n "sysupgrade recovery readiness を検証" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
  confirm_assert_line="$(rg -n "sysupgrade 実行 confirmation を検証" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
  upgrade_import_line="$(rg -n "ansible.builtin.import_tasks: upgrade.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
  if [[ -n "$verify_import_line" && -n "$backup_import_line" && -n "$recovery_assert_line" && -n "$confirm_assert_line" && -n "$upgrade_import_line" ]]; then
    if ! ((verify_import_line < backup_import_line && backup_import_line < recovery_assert_line && recovery_assert_line < confirm_assert_line && confirm_assert_line < upgrade_import_line)); then
      record_failure "openwrt_sysupgrade flow must be verify.yml -> backup.yml -> recovery readiness -> confirmation -> upgrade.yml"
    fi
  else
    record_failure "openwrt_sysupgrade flow line ordering could not be determined"
  fi
fi

echo
echo "== skill review: openwrt_sysupgrade manifest summary flow =="
manifest_tasks_file="ansible/openwrt/roles/openwrt_sysupgrade/tasks/manifest.yml"
manifest_changed=0
for path in "${changed_files[@]}"; do
  case "$path" in
    "$manifest_tasks_file"|"ansible/openwrt/roles/openwrt_sysupgrade/tasks/main.yml"|"ansible/openwrt/roles/openwrt_sysupgrade/README.md")
      manifest_changed=1
      ;;
  esac
done
if [[ "$manifest_changed" == "1" ]]; then
  if [[ ! -f "$manifest_tasks_file" ]]; then
    record_failure "openwrt_sysupgrade manifest summary flow changed but manifest.yml is missing"
  else
    manifest_required_patterns=(
      "owrt_manifest_phase in \\[\"pre\", \"post\"\\]"
      "ansible.builtin.raw:"
      "command -v apk"
      "command -v opkg"
      "apk info"
      "opkg list-installed | cut -d\" \" -f1"
      "network firewall dnsmasq rpcbind nfsd frr banip prometheus-node-exporter-lua uhttpd log"
      "owrt_sysupgrade_manifests:"
      "service_status_lines"
      "package_count"
    )
    for pattern in "${manifest_required_patterns[@]}"; do
      if ! rg -q "$pattern" "$manifest_tasks_file"; then
        record_failure "openwrt_sysupgrade manifest lost expected pattern: ${pattern}"
      fi
    done

    if rg -n "oldString:|newString:" "$manifest_tasks_file"; then
      record_failure "openwrt_sysupgrade manifest contains leaked OpenCode edit metadata"
    fi
  fi

  if [[ -f "$main_tasks_file" ]]; then
    verify_import_line="$(rg -n "ansible.builtin.import_tasks: verify.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    pre_manifest_line="$(rg -n "owrt_manifest_phase: pre" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    backup_import_line="$(rg -n "ansible.builtin.import_tasks: backup.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    upgrade_import_line="$(rg -n "ansible.builtin.import_tasks: upgrade.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    reset_connection_line="$(rg -n "ansible.builtin.include_tasks: reset_connection.yml" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    post_manifest_line="$(rg -n "owrt_manifest_phase: post" "$main_tasks_file" | head -n1 | cut -d: -f1 || true)"
    if [[ -n "$verify_import_line" && -n "$pre_manifest_line" && -n "$backup_import_line" ]]; then
      if ! ((verify_import_line < pre_manifest_line && pre_manifest_line < backup_import_line)); then
        record_failure "openwrt_sysupgrade pre manifest must run after verify.yml and before backup.yml"
      fi
    else
      record_failure "openwrt_sysupgrade pre manifest line ordering could not be determined"
    fi
    if [[ -n "$upgrade_import_line" && -n "$reset_connection_line" && -n "$post_manifest_line" ]]; then
      if ! ((upgrade_import_line < reset_connection_line && reset_connection_line < post_manifest_line)); then
        record_failure "openwrt_sysupgrade post manifest must run after upgrade.yml and connection reset"
      fi
    else
      record_failure "openwrt_sysupgrade post manifest line ordering could not be determined"
    fi
  fi

  reset_connection_tasks_file="ansible/openwrt/roles/openwrt_sysupgrade/tasks/reset_connection.yml"
  if [[ -f "$reset_connection_tasks_file" ]]; then
    if ! rg -q "ansible.builtin.meta: reset_connection" "$reset_connection_tasks_file"; then
      record_failure "openwrt_sysupgrade reset_connection.yml must contain meta reset_connection"
    fi
    if rg -n "when:" "$reset_connection_tasks_file"; then
      record_failure "openwrt_sysupgrade reset_connection.yml must not put when directly on meta reset_connection"
    fi
  else
    record_failure "openwrt_sysupgrade reset_connection.yml is missing"
  fi
fi

echo
echo "== skill review: Ansible role task files are task lists =="
role_task_files=()
for path in "${changed_files[@]}"; do
  case "$path" in
    ansible/arm64/roles/*/tasks/*.yml|ansible/arm64/roles/*/tasks/*.yaml|ansible/openwrt/roles/*/tasks/*.yml|ansible/openwrt/roles/*/tasks/*.yaml)
      if [[ -f "$path" ]]; then
        role_task_files+=("$path")
      fi
      ;;
  esac
done
if ((${#role_task_files[@]} > 0)); then
  if ! python3 - "${role_task_files[@]}" <<'PY'
import sys
from pathlib import Path

import yaml

failures = []
valid_task_keys = {
    "name",
    "action",
    "args",
    "become",
    "become_user",
    "block",
    "changed_when",
    "check_mode",
    "delegate_facts",
    "delegate_to",
    "environment",
    "failed_when",
    "ignore_errors",
    "loop",
    "loop_control",
    "notify",
    "register",
    "rescue",
    "run_once",
    "tags",
    "until",
    "vars",
    "when",
    "with_items",
}

for file_name in sys.argv[1:]:
    path = Path(file_name)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - review script should report parse context.
        failures.append(f"{path}: YAML parse failed: {exc}")
        continue

    if data is None:
        continue
    if not isinstance(data, list):
        failures.append(f"{path}: role task file must be a YAML list, not {type(data).__name__}")
        continue

    for index, task in enumerate(data, start=1):
        if not isinstance(task, dict):
            failures.append(f"{path}: task #{index} must be a mapping, not {type(task).__name__}")
            continue
        if "name" not in task and "block" not in task:
            failures.append(f"{path}: task #{index} should include name or block")
        module_like_keys = [
            key for key in task
            if isinstance(key, str)
            and key not in valid_task_keys
            and (key.startswith("ansible.") or "." not in key)
        ]
        if not module_like_keys:
            failures.append(f"{path}: task #{index} has no obvious module/action key")

        include_tasks = task.get("ansible.builtin.include_tasks")
        if isinstance(include_tasks, str) and "tags" in task:
            failures.append(
                f"{path}: task #{index}: include_tasks with task-level tags must use apply.tags "
                "so tags propagate to included tasks"
            )
        if isinstance(include_tasks, dict) and "tags" in task:
            apply_value = include_tasks.get("apply")
            if not isinstance(apply_value, dict) or "tags" not in apply_value:
                failures.append(
                    f"{path}: task #{index}: include_tasks with task-level tags must include "
                    "ansible.builtin.include_tasks.apply.tags"
                )

if failures:
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("role task YAML structure ok")
PY
  then
    record_failure "Ansible role task YAML structure check failed"
  fi
else
  echo "no changed role task files"
fi

echo
echo "== skill review: openwrt_package include vars =="
openwrt_package_task_files=()
for path in "${changed_files[@]}"; do
  case "$path" in
    ansible/openwrt/roles/*/tasks/*.yml|ansible/openwrt/roles/*/tasks/*.yaml)
      if [[ -f "$path" ]]; then
        openwrt_package_task_files+=("$path")
      fi
      ;;
  esac
done
if ((${#openwrt_package_task_files[@]} > 0)); then
  if ! python3 - "${openwrt_package_task_files[@]}" <<'PY'
import sys
from pathlib import Path

import yaml

failures = []

for file_name in sys.argv[1:]:
    path = Path(file_name)
    try:
        tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - review script should report parse context.
        failures.append(f"{path}: YAML parse failed while checking openwrt_package include vars: {exc}")
        continue

    if not isinstance(tasks, list):
        continue

    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue

        include_role = task.get("ansible.builtin.include_role")
        if not isinstance(include_role, dict):
            continue
        if include_role.get("name") != "openwrt_package":
            continue

        task_name = task.get("name", f"task #{index}")
        for forbidden_key in ("loop", "loop_control"):
            if forbidden_key in task:
                failures.append(
                    f"{path}: {task_name}: openwrt_package include must not keep {forbidden_key}"
                )

        vars_value = task.get("vars")
        if not isinstance(vars_value, dict):
            failures.append(f"{path}: {task_name}: openwrt_package include must use task-level vars")
            continue

        for key in include_role:
            if key == "name":
                continue
            if key == "vars" or key.startswith("openwrt_package_"):
                failures.append(
                    f"{path}: {task_name}: {key} is nested under include_role; move it to task-level vars"
                )

        for key in task:
            if key.startswith("openwrt_package_"):
                failures.append(
                    f"{path}: {task_name}: {key} is a task-level key; move it under task-level vars"
                )

        if "openwrt_package_names" not in vars_value:
            failures.append(f"{path}: {task_name}: task-level vars must define openwrt_package_names")
        else:
            package_names = vars_value.get("openwrt_package_names")
            if isinstance(package_names, str):
                stripped = package_names.strip()
                if not (stripped.startswith("{{") and stripped.endswith("}}")):
                    failures.append(
                        f"{path}: {task_name}: openwrt_package_names string must render a Jinja expression"
                    )
            elif not isinstance(package_names, list):
                failures.append(
                    f"{path}: {task_name}: openwrt_package_names must be a list or rendered Jinja list expression"
                )

        if vars_value.get("openwrt_package_state") not in {"present", "absent"}:
            failures.append(
                f"{path}: {task_name}: task-level vars must set openwrt_package_state to present or absent"
            )

        if (
            vars_value.get("openwrt_package_state") == "present"
            and "openwrt_package_update_cache" not in vars_value
        ):
            failures.append(
                f"{path}: {task_name}: present package tasks must set openwrt_package_update_cache"
            )

if failures:
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("openwrt_package include vars structure ok")
PY
  then
    record_failure "openwrt_package include vars structure check failed"
  fi

  if rg -n '^\s*openwrt_package_names:\s*openwrt_[A-Za-z0-9_]+_packages\s*$' "${openwrt_package_task_files[@]}"; then
    record_failure "openwrt_package_names must pass list variables with Jinja, e.g. {{ openwrt_example_packages }}, not a bare string literal"
  fi
else
  echo "no changed OpenWrt task files"
fi

echo
echo "== skill review: OpenWrt post-upgrade collector package-manager boundary =="
collector_file=".agents/skills/homecluster-openwrt-postupgrade-check/scripts/collect-openwrt-postupgrade.sh"
collector_schema=".agents/skills/homecluster-openwrt-postupgrade-check/references/postupgrade-output-schema.md"
collector_changed=0
for path in "${changed_files[@]}"; do
  case "$path" in
    "$collector_file"|"$collector_schema"|.agents/skills/homecluster-openwrt-postupgrade-check/SKILL.md)
      collector_changed=1
      ;;
  esac
done
if [[ "$collector_changed" == "1" && -f "$collector_file" ]]; then
  if ! python3 - "$collector_file" "$collector_schema" <<'PY'
import re
import sys
from pathlib import Path

collector_path = Path(sys.argv[1])
schema_path = Path(sys.argv[2])
text = collector_path.read_text(encoding="utf-8")
schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else ""
failures = []

def line_number(offset):
    return text.count("\n", 0, offset) + 1

def in_run_router_single_quote(offset):
    marker = "run_router '"
    start = text.rfind(marker, 0, offset)
    if start < 0:
        return False
    quote_start = start + len(marker)
    quote_end = text.find("')", quote_start)
    return quote_end != -1 and quote_start <= offset < quote_end

for match in re.finditer(r"command\s+-v\s+(apk|opkg)\b", text):
    if not in_run_router_single_quote(match.start()):
        failures.append(
            f"{collector_path}:{line_number(match.start())}: package-manager command detection "
            "must run on the router through run_router"
        )

for pattern in (
    r'echo\s+"\$release_raw"\s*\|',
    r'\$release_raw\s*\|\s*grep',
    r'\$release_raw\s*\|\s*cut',
):
    for match in re.finditer(pattern, text):
        failures.append(
            f"{collector_path}:{line_number(match.start())}: release_raw is capture_json output; "
            "decode it with jq or read release on the router"
        )

if "selected_for_packages" not in text:
    failures.append(f"{collector_path}: router.package_manager.selected_for_packages is missing")
if "selected_for_packages" not in schema:
    failures.append(f"{schema_path}: selected_for_packages is not documented")

if "apk info" in text and 'opkg list-installed | cut -d" " -f1' in text:
    pass
else:
    failures.append(f"{collector_path}: collector must preserve both apk info and opkg package listing")

if failures:
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("OpenWrt post-upgrade collector package-manager boundary ok")
PY
  then
    record_failure "OpenWrt post-upgrade collector package-manager boundary check failed"
  fi
else
  echo "collector not changed"
fi

echo
echo "== skill review: OpenWrt package-manager boundary audit =="
package_boundary_checker=".agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py"
if [[ -x "$package_boundary_checker" ]]; then
  if ! "$package_boundary_checker"; then
    record_failure "OpenWrt package-manager boundary audit failed"
  fi
else
  record_failure "OpenWrt package-manager boundary checker is missing or not executable"
fi

echo
echo "== skill review: OpenWrt post-upgrade source contract =="
postupgrade_contract_checker=".agents/skills/homecluster-openwrt-postupgrade-check/scripts/check_openwrt_postupgrade_source_contract.py"
if [[ -x "$postupgrade_contract_checker" ]]; then
  if ! "$postupgrade_contract_checker"; then
    record_failure "OpenWrt post-upgrade source contract audit failed"
  fi
else
  record_failure "OpenWrt post-upgrade source contract checker is missing or not executable"
fi

echo
echo "== skill review: runbook read-only boundary =="
if [[ "${SKIP_RUNBOOK_DIRTY_CHECK:-0}" != "1" ]]; then
  runbook_dir="$(cd "$repo_root/../homecluster-runbook" 2>/dev/null && pwd || true)"
  runbook_scope="docs/operation"
  if [[ -n "$runbook_dir" && -d "$runbook_dir/.git" && -d "$runbook_dir/$runbook_scope" ]]; then
    if ! git -C "$runbook_dir" diff --quiet -- "$runbook_scope"; then
      echo "tracked runbook files changed during an infra implementation run: $runbook_scope" >&2
      echo "Revert it or report proposed wording instead. Set SKIP_RUNBOOK_DIRTY_CHECK=1 only when explicitly editing the runbook." >&2
      record_failure "tracked runbook operation docs changed during an infra implementation run"
    fi
  else
    echo "sibling runbook repository not found; skipping"
  fi
else
  echo "skipped by SKIP_RUNBOOK_DIRTY_CHECK=1"
fi

if ((failures > 0)); then
  echo
  echo "skill review failed before repository static checks; fix the failures above and rerun this script." >&2
  exit 1
fi

echo
echo "== skill review: repository static check =="
bash scripts/ci/static-check.sh

echo
echo "== skill review: OpenWrt syntax checks with example inventory =="
ansible-playbook --syntax-check ansible/openwrt/site.yml -i examples/inventory.yml
ansible-playbook --syntax-check ansible/openwrt/playbooks/upgrade-openwrt.yml -i examples/inventory.yml

echo
echo "skill review ok"
