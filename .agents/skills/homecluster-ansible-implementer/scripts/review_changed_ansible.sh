#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

echo "== skill review: diff whitespace =="
git diff --check

echo
echo "== skill review: changed files =="
mapfile -t changed_files < <(
  {
    git diff --name-only --diff-filter=ACMRT HEAD --
    git ls-files --others --exclude-standard
  } | sort -u
)
if ((${#changed_files[@]} == 0)); then
  echo "no tracked file changes"
else
  printf '%s\n' "${changed_files[@]}"
fi

echo
echo "== skill review: public redaction patterns =="
public_files=()
for path in "${changed_files[@]}"; do
  case "$path" in
    *.md|README|README.*|docs/*|.agents/skills/*/SKILL.md|.agents/skills/*/references/*.md|ansible/*/roles/*/README.md|opencode.json)
      if [[ -f "$path" ]]; then
        public_files+=("$path")
      fi
      ;;
  esac
done

if ((${#public_files[@]} > 0)); then
  redaction_patterns=(
    "/"home"/[A-Za-z0-9._-]+"
    "10\\.[0-9]+\\.[0-9]+\\.[0-9]+"
    "192\\.168\\."
    "home""-router"
    "k3s""-prd"
    "pico""claw"
    "soft""ether"
  )
  for pattern in "${redaction_patterns[@]}"; do
    if rg -n "$pattern" "${public_files[@]}"; then
      echo "public redaction pattern found in changed files" >&2
      exit 1
    fi
  done
else
  echo "no changed public docs/config files"
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
  )
  for key in "${required_keys[@]}"; do
    if ! rg -q "^${key}:" "$defaults_file"; then
      echo "missing required existing default key: ${key}" >&2
      exit 1
    fi
  done
  echo "openwrt_sysupgrade defaults keys present"
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
