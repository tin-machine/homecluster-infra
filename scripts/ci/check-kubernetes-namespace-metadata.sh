#!/usr/bin/env bash
# Plan the checked-in Namespace resources with the locked real provider.
# This is not an existing-state migration test or a live cluster check.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT

# Archive only committed public source: never copy local state, kubeconfig,
# generated inventory, tfvars, or a developer's .terraform directory.
git -C "${repo_root}" archive HEAD terraform clusters | tar -x -C "${scratch}"
mkdir -p "${scratch}/home" "${scratch}/plugin-cache"

run_terraform() {
  local root="$1"
  shift
  env -i PATH="${PATH}" HOME="${scratch}/home" \
    TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1 \
    TF_PLUGIN_CACHE_DIR="${scratch}/plugin-cache" \
    terraform -chdir="${scratch}/terraform/env/${root}" "$@"
}

for root in common-crds staging; do
  run_terraform "${root}" init -backend=false -input=false -lockfile=readonly
  run_terraform "${root}" test -no-color -filter=tests/namespace_metadata.tftest.hcl
done
run_terraform staging test -no-color -filter=tests/registry_namespace_metadata.tftest.hcl
