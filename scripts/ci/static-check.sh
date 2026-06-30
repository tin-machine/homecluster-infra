#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

fail=0
strict_local_scan="${STATIC_CHECK_STRICT_LOCAL:-${STATIC_CHECK_SCAN_IGNORED:-0}}"

mapfile -t openwrt_playbooks < <(
  find ansible/openwrt -maxdepth 2 -type f \
    \( -path '*/site.yml' -o -path '*/site.yaml' -o \
       -path '*/playbooks/*.yml' -o -path '*/playbooks/*.yaml' \) -print | sort
)
mapfile -t arm64_playbooks < <(
  find ansible/arm64 -maxdepth 2 -type f \
    \( -path '*/site.yml' -o -path '*/site.yaml' -o \
       -path '*/playbooks/*.yml' -o -path '*/playbooks/*.yaml' \) -print | sort
)

print_section() {
  printf '\n== %s ==\n' "$1"
}

run_ansible_entrypoints() {
  local mode="$1"
  local roles_path="$2"
  shift 2

  local playbook
  for playbook in "$@"; do
    ANSIBLE_ROLES_PATH="${roles_path}" \
      ansible-playbook -i examples/inventory.yml "${playbook}" "${mode}"
  done
}

report_matches() {
  local title="$1"
  local matches="$2"
  if [ -n "${matches}" ]; then
    print_section "${title}"
    printf '%s\n' "${matches}"
    fail=1
  fi
}

list_scan_files() {
  if [ "${strict_local_scan}" = "1" ]; then
    find . -path ./.git -prune -o -type f -print | sed 's#^\./##' | sort
  else
    git ls-files --cached --others --exclude-standard | sort
  fi
}

list_changed_files() {
  local base_ref="${STATIC_CHECK_BASE_REF:-origin/main}"
  local base_commit=""
  if git rev-parse --verify --quiet "${base_ref}" >/dev/null; then
    base_commit="$(git merge-base "${base_ref}" HEAD)"
  fi

  {
    if [ -n "${base_commit}" ]; then
      git diff --name-only --diff-filter=ACMRT "${base_commit}..HEAD" --
    elif git rev-parse --verify --quiet HEAD^ >/dev/null; then
      git diff --name-only --diff-filter=ACMRT HEAD^..HEAD --
    fi
    git diff --name-only --diff-filter=ACMRT HEAD --
    git ls-files --others --exclude-standard
  } | sort -u
}

file_size_bytes() {
  wc -c <"$1" | tr -d '[:space:]'
}

mapfile -t scan_files < <(list_scan_files)
mapfile -t changed_files < <(list_changed_files)

print_section "scan scope"
if [ "${strict_local_scan}" = "1" ]; then
  echo "strict local worktree, including ignored files"
else
  echo "tracked files and untracked non-ignored files"
fi
echo "files: ${#scan_files[@]}"

print_section "hard exclude file scan"
hard_matches="$(
  printf '%s\n' "${scan_files[@]}" |
    grep -E '(^|/)(\.sops\.yaml|terraform\.tfstate(\..*)?|[^/]*\.tfvars(\.json)?|[^/]*secret[^/]*\.(ya?ml|json)|id_rsa|id_ed25519|[^/]*\.(pem|key))$|(^|/)\.terraform/' ||
    true
)"
report_matches "hard excluded files found" "${hard_matches}"

print_section "large file scan"
large_matches="$(
  for path in "${scan_files[@]}"; do
    [ -f "${path}" ] || continue
    if [ "$(file_size_bytes "${path}")" -gt 5242880 ]; then
      printf '%s\n' "${path}"
    fi
  done
)"
report_matches "files larger than 5M found" "${large_matches}"

print_section "redaction pattern scan"
redaction_pattern='10\.10\.|10\.11\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|f[c-d][0-9a-fA-F]{2}(:[0-9a-fA-F]{1,4}){2,7}::?/[0-9]{1,3}|home-router|rpi[0-9]-[0-9]{2}|k3s-prd|backup-disk|tin-machine\.io|github\.com/tin-machine|desktop-lab|PRIVATE KEY|BEGIN [A-Z ]*PRIVATE KEY|picoclaw|k3s_iscsi_storage|terraform_auto_apply|common/codex_cli|common/nfs_mount|softether'
redaction_matches="$(
  redaction_files=()
  for path in "${scan_files[@]}"; do
    [ -f "${path}" ] || continue
    [ "${path}" != "scripts/ci/static-check.sh" ] || continue
    redaction_files+=("${path}")
  done
  if [ "${#redaction_files[@]}" -gt 0 ]; then
    grep -nIE --binary-files=without-match "${redaction_pattern}" "${redaction_files[@]}" |
      grep -vE 'github\.com/tin-machine/homecluster-infra(/|$)' || true
  fi
)"
report_matches "redaction pattern matches found" "${redaction_matches}"

print_section "k3s_converge check-only source validator"
k3s_converge_validator_pattern='(^|[;&|[:space:]])(systemctl[[:space:]]+(start|restart|enable|disable)|service[[:space:]]+[^[:space:]]+[[:space:]]+(start|restart|enable|disable)|mount[[:space:]]+|umount[[:space:]]+|iscsiadm([[:space:]]|$)|terraform[[:space:]]+(apply|destroy)|kubectl[[:space:]].*(delete|apply|patch|create)|rm[[:space:]]+|wipefs([[:space:]]|$)|mkfs([.[:alnum:]_-]*[[:space:]]|$))'
k3s_converge_validator_matches="$(
  k3s_converge_files=()
  for path in "${scan_files[@]}"; do
    [ -f "${path}" ] || continue
    if [[ "${path}" == *"ansible/arm64/roles/k3s_converge_check"* ]]; then
      k3s_converge_files+=("${path}")
    fi
  done
  if [ "${#k3s_converge_files[@]}" -gt 0 ]; then
    grep -nIE --binary-files=without-match "${k3s_converge_validator_pattern}" "${k3s_converge_files[@]}" || true
  fi
)"
report_matches "k3s_converge check-only violations found" "${k3s_converge_validator_matches}"


print_section "terraform and helm values redaction scan"
terraform_values_redaction_pattern='192\.168\.|10\.10\.|10\.11\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|fd[0-9a-fA-F]{2}:|fdd[0-9a-fA-F]:|BEGIN .*PRIVATE KEY|AKIA[0-9A-Z]{16}|xox[baprs]-|gh[pousr]_[A-Za-z0-9_]+|@[^[:space:]]+\.[A-Za-z]{2,}'
terraform_values_redaction_matches="$(
  terraform_values_redaction_files=()
  for path in "${scan_files[@]}"; do
    [ -f "${path}" ] || continue
    case "${path}" in
      terraform/*|clusters/*)
        terraform_values_redaction_files+=("${path}")
        ;;
    esac
  done
  if [ "${#terraform_values_redaction_files[@]}" -gt 0 ]; then
    grep -nIE --binary-files=without-match "${terraform_values_redaction_pattern}" "${terraform_values_redaction_files[@]}" || true
  fi
)"
report_matches "terraform or helm values redaction matches found" "${terraform_values_redaction_matches}"

print_section "trailing whitespace scan"
trailing_matches="$(
  trailing_files=()
  for path in "${scan_files[@]}"; do
    [ -f "${path}" ] || continue
    case "${path}" in
      *.json|*.lock.hcl)
        continue
        ;;
    esac
    trailing_files+=("${path}")
  done
  if [ "${#trailing_files[@]}" -gt 0 ]; then
    grep -nIE --binary-files=without-match '[[:blank:]]$' "${trailing_files[@]}" || true
  fi
)"
report_matches "trailing whitespace found" "${trailing_matches}"

print_section "ansible jinja compatibility scan"
ansible_jinja_compat_matches="$(
  grep -nR --include='*.yml' --include='*.yaml' -E '\bis[[:space:]]+list\b' ansible .agents 2>/dev/null || true
)"
report_matches "unsupported Ansible/Jinja list test found" "${ansible_jinja_compat_matches}"

print_section "markdownlint"
markdownlint_files=()
for path in "${changed_files[@]}"; do
  [ -f "${path}" ] || continue
  case "${path}" in
    *.md|README|README.*)
      markdownlint_files+=("${path}")
      ;;
  esac
done
if [ "${#markdownlint_files[@]}" -gt 0 ] && command -v markdownlint >/dev/null 2>&1; then
  if ! markdownlint --disable MD013 -- "${markdownlint_files[@]}"; then
    fail=1
  fi
else
  echo "markdownlint not found or no changed markdown files; skipping"
fi
if command -v python3 >/dev/null 2>&1; then
  if ! python3 scripts/ci/check-changed-markdown-style.py "${markdownlint_files[@]}"; then
    fail=1
  fi
else
  echo "python3 not found; skipping changed markdown style check"
fi

print_section "terraform fmt"
if command -v terraform >/dev/null 2>&1; then
  terraform fmt -check -recursive
else
  echo "terraform not found; skipping fmt check"
fi

print_section "optional terraform validate"
if [ "${RUN_TERRAFORM_VALIDATE:-0}" = "1" ]; then
  terraform_data_root="$(mktemp -d)"
  trap 'rm -rf "${terraform_data_root}"' EXIT

  for terraform_env in \
    terraform/env/common-crds \
    terraform/env/common-addons \
    terraform/env/common-certificates \
    terraform/env/staging
  do
    terraform_env_name="$(basename "${terraform_env}")"
    TF_DATA_DIR="${terraform_data_root}/${terraform_env_name}" \
      terraform -chdir="${terraform_env}" init \
        -backend=false \
        -input=false \
        -lockfile=readonly
    TF_DATA_DIR="${terraform_data_root}/${terraform_env_name}" \
      terraform -chdir="${terraform_env}" validate
  done
else
  echo "set RUN_TERRAFORM_VALIDATE=1 to run backend-free terraform validate"
fi

print_section "python syntax"
if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile ansible/openwrt/roles/openwrt_pxe_client_catalog/filter_plugins/openwrt_pxe_client_catalog.py
  python3 -m py_compile scripts/ansible/convert_openwrt_package_task.py
  python3 -m py_compile scripts/ci/check-changed-markdown-style.py
  python3 -m py_compile scripts/ci/check-openwrt-pxe-ansible-pull-chain.py
  python3 -m py_compile .agents/skills/homecluster-ansible-implementer/scripts/check_opencode_session_export.py
  python3 -m py_compile .agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py
  python3 -m py_compile .agents/skills/homecluster-openwrt-postupgrade-check/scripts/check_openwrt_postupgrade_source_contract.py
  python3 scripts/ci/check-openwrt-pxe-client-catalog.py
  python3 scripts/ci/check-openwrt-pxe-ansible-pull-chain.py
  python3 scripts/ansible/convert_openwrt_package_task.py --self-test
  .agents/skills/homecluster-ansible-implementer/scripts/check_opencode_session_export.py --self-test
  .agents/skills/homecluster-openwrt-package-boundary-auditor/scripts/check_openwrt_package_boundaries.py --self-test
  .agents/skills/homecluster-openwrt-postupgrade-check/scripts/check_openwrt_postupgrade_source_contract.py --self-test
  scripts/docs/context_hygiene_check.py
else
  echo "python3 not found; skipping py_compile"
fi

print_section "optional ansible syntax"
if [ "${RUN_ANSIBLE_SYNTAX:-0}" = "1" ]; then
  run_ansible_entrypoints \
    --syntax-check \
    "ansible/openwrt/roles:${HOME}/.ansible/roles:/usr/share/ansible/roles" \
    "${openwrt_playbooks[@]}"
  run_ansible_entrypoints \
    --syntax-check \
    "ansible/arm64/roles:${HOME}/.ansible/roles:/usr/share/ansible/roles" \
    "${arm64_playbooks[@]}"
else
  echo "set RUN_ANSIBLE_SYNTAX=1 to run local ansible syntax checks"
fi

print_section "optional ansible task expansion"
if [ "${RUN_ANSIBLE_LIST_TASKS:-0}" = "1" ]; then
  run_ansible_entrypoints \
    --list-tasks \
    "ansible/openwrt/roles:${HOME}/.ansible/roles:/usr/share/ansible/roles" \
    "${openwrt_playbooks[@]}"
  run_ansible_entrypoints \
    --list-tasks \
    "ansible/arm64/roles:${HOME}/.ansible/roles:/usr/share/ansible/roles" \
    "${arm64_playbooks[@]}"
else
  echo "set RUN_ANSIBLE_LIST_TASKS=1 to list all local ansible entrypoints"
fi

if [ "${fail}" -ne 0 ]; then
  exit 1
fi

print_section "static check ok"
