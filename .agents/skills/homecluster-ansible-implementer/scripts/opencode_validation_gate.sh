#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'USAGE'
Usage:
  opencode_validation_gate.sh [--skip-runbook-dirty-check]

Runs the validation sequence expected after OpenCode/local LLM changes and prints one compact JSON
object. Detailed command logs are written under /tmp and referenced by log_dir.
USAGE
}

skip_runbook_dirty_check=0

while (($# > 0)); do
  case "$1" in
    --skip-runbook-dirty-check)
      skip_runbook_dirty_check=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$repo_root" ]]; then
  echo '{"ok":false,"failed_step":"repo-root","exit_code":128,"summary":"not inside a git repository","log_dir":"","commands_run":[],"commands_not_run":[]}'
  exit 128
fi
cd "$repo_root" || exit 128

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/opencode-validation-gate.XXXXXX")"

step_names=(
  "diff-check"
  "static-check"
  "ansible-syntax-and-list-tasks"
  "openwrt-package-boundary-audit"
  "openwrt-postupgrade-source-contract"
  "ansible-skill-review"
)
step_commands=(
  "git diff --check"
  "bash scripts/ci/static-check.sh"
  "RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 bash scripts/ci/static-check.sh"
  "./.agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py"
  "./.agents/skills/homecluster-openwrt-postupgrade-check/scripts/check_openwrt_postupgrade_source_contract.py"
  "./.agents/skills/homecluster-ansible-implementer/scripts/review_changed_ansible.sh"
)

if [[ -d ansible/openwrt/roles/openwrt_package ]]; then
  smoke_playbook="${log_dir}/openwrt-package-smoke.yml"
  cat >"$smoke_playbook" <<'EOF'
---
- hosts: localhost
  gather_facts: false
  vars:
    openwrt_pkg_manager: opkg
    openwrt_package_names:
      - zlib
  roles:
    - role: openwrt_package
EOF
  step_names+=("openwrt-package-syntax")
  step_commands+=("ansible-playbook -i localhost, -c local ${smoke_playbook} --syntax-check")
  step_names+=("openwrt-package-list-tasks")
  step_commands+=("ansible-playbook -i localhost, -c local ${smoke_playbook} --list-tasks")
fi

commands_run=()
failed_step=""
failed_exit=0

run_step() {
  local name="$1"
  local command_text="$2"
  local log_path="${log_dir}/${name}.log"

  commands_run+=("${name}: ${command_text}")
  if [[ "$name" == "ansible-skill-review" && "$skip_runbook_dirty_check" == "1" ]]; then
    SKIP_RUNBOOK_DIRTY_CHECK=1 bash -lc "$command_text" >"$log_path" 2>&1
  else
    bash -lc "$command_text" >"$log_path" 2>&1
  fi
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    failed_step="$name"
    failed_exit="$status"
    return "$status"
  fi
  return 0
}

failed_index=-1
for index in "${!step_names[@]}"; do
  if ! run_step "${step_names[$index]}" "${step_commands[$index]}"; then
    failed_index="$index"
    break
  fi
done

commands_not_run=()
if [[ "$failed_index" -ge 0 ]]; then
  for ((i = failed_index + 1; i < ${#step_names[@]}; i++)); do
    commands_not_run+=("${step_names[$i]}: ${step_commands[$i]}")
  done
fi

commands_run_file="${log_dir}/commands_run.txt"
commands_not_run_file="${log_dir}/commands_not_run.txt"
if [[ "${#commands_run[@]}" -gt 0 ]]; then
  printf '%s\n' "${commands_run[@]}" >"$commands_run_file"
else
  : >"$commands_run_file"
fi
if [[ "${#commands_not_run[@]}" -gt 0 ]]; then
  printf '%s\n' "${commands_not_run[@]}" >"$commands_not_run_file"
else
  : >"$commands_not_run_file"
fi

failed_log=""
if [[ -n "$failed_step" ]]; then
  failed_log="${log_dir}/${failed_step}.log"
fi

FAILED_STEP="$failed_step" \
FAILED_EXIT="$failed_exit" \
LOG_DIR="$log_dir" \
python3 - "$failed_log" "$commands_run_file" "$commands_not_run_file" <<'PY'
import json
import os
import re
import sys

failed_log, commands_run_file, commands_not_run_file = sys.argv[1:4]
failed_step = os.environ.get("FAILED_STEP", "")
failed_exit = int(os.environ.get("FAILED_EXIT", "0"))
log_dir = os.environ.get("LOG_DIR", "")

def read_lines(path):
    if not path:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except FileNotFoundError:
        return []

def summarize_failure(lines):
    if not lines:
        return "validation command failed without captured output"

    text = "\n".join(lines)
    marker = "== trailing whitespace found =="
    if marker in text:
        after = text.split(marker, 1)[1].split("\n== ", 1)[0]
        matches = [line.strip() for line in after.splitlines() if line.strip()]
        return "trailing whitespace found: " + "; ".join(matches[:3])

    interesting = [
        line.strip()
        for line in lines
        if re.search(r"(FAILED|ERROR|Error|error|failed|fatal|syntax|traceback)", line)
    ]
    if interesting:
        return "; ".join(interesting[:6])[:900]

    tail = [line.strip() for line in lines if line.strip()][-12:]
    return "; ".join(tail)[:900]

commands_run = read_lines(commands_run_file)
commands_not_run = read_lines(commands_not_run_file)
ok = failed_step == ""

result = {
    "ok": ok,
    "failed_step": None if ok else failed_step,
    "exit_code": 0 if ok else failed_exit,
    "summary": "all validation steps passed" if ok else summarize_failure(read_lines(failed_log)),
    "log_dir": log_dir,
    "commands_run": commands_run,
    "commands_not_run": commands_not_run,
}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
sys.exit(0 if ok else failed_exit)
PY

exit "$?"
