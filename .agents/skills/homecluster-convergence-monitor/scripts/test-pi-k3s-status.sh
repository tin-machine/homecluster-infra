#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../../.." && pwd)"
status_script="$repo_root/scripts/pi-k3s-status"
status_impl="$script_dir/pi-k3s-status"
resolver="$script_dir/pi_k3s_inventory_targets.py"

bash -n "$status_script"
bash -n "$status_impl"
grep -Fq '.agents/skills/homecluster-convergence-monitor/scripts/pi-k3s-status' "$status_script"
grep -Fq 'repo_root="$(cd "$skill_dir/../../.." && pwd)"' "$status_impl"
python3 -m py_compile "$resolver" "$script_dir/test_pi_k3s_inventory_targets.py"
python3 -m unittest discover -s "$script_dir" -p 'test_pi_k3s_inventory_targets.py'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat >"$tmp/bin/ansible-inventory" <<'PY'
#!/usr/bin/env python3
import json
print(json.dumps({
    "_meta": {
        "hostvars": {
            "control-a": {
                "ansible_host": "control.example.invalid",
                "ansible_user": "ansible",
            },
            "agent-a": {
                "ansible_host": "agent.example.invalid",
                "ansible_user": "ops",
            },
        }
    },
    "k3s_stg_server": {"hosts": ["control-a"]},
    "k3s_stg_agents": {"hosts": ["agent-a"]},
}))
PY
chmod +x "$tmp/bin/ansible-inventory"

cat >"$tmp/collector" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
env | grep -E '^(MONITOR_CONTROL_SSH|MONITOR_NODE_SSH_LIST|MONITOR_EXPECTED_NODES|HOMECLUSTER_K3S_NODE_TARGET_MAP)=' | sort >"$STATUS_CAPTURE"
cat <<'JSON'
{"generated_at":"2026-07-21T00:00:00Z","assessment":{"status":"healthy","issues":[]},"nodes":{"ready_count":2,"count":2,"items":[]},"pods":{"non_running":[],"running_not_ready":[]},"node_exporter":{"ready":2,"desired":2},"signals":[]}
JSON
SH
chmod +x "$tmp/collector"

touch "$tmp/inventory.yml"
set +e
output="$(
  PATH="$tmp/bin:$PATH" \
  STATUS_CAPTURE="$tmp/capture.env" \
  HOMECLUSTER_ANSIBLE_INVENTORY="$tmp/inventory.yml" \
  HOMECLUSTER_K3S_COLLECTOR="$tmp/collector" \
  HOMECLUSTER_K3S_DIAGNOSER="$tmp/missing-diagnoser" \
  HOMECLUSTER_K3S_CLASSIFIER="$tmp/missing-classifier" \
  HOMECLUSTER_K3S_CASE_LIBRARY="$tmp/missing-cases" \
  bash "$status_script" --json
)"
rc=$?
set -e
[ "$rc" -eq 0 ]

printf '%s\n' "$output" | jq -e '
  .status == "healthy" and
  .target_resolution == "ansible_inventory" and
  .target_control_host == "control-a" and
  .target_node_hosts == ["control-a", "agent-a"] and
  .nodes_ready == 2 and
  .nodes_total == 2
' >/dev/null

grep -Fxq 'MONITOR_CONTROL_SSH=ansible@control.example.invalid' "$tmp/capture.env"
grep -Fxq 'MONITOR_EXPECTED_NODES=2' "$tmp/capture.env"
grep -Fxq 'MONITOR_NODE_SSH_LIST=ansible@control.example.invalid ops@agent.example.invalid' "$tmp/capture.env"
grep -Eq 'HOMECLUSTER_K3S_NODE_TARGET_MAP=.*control-a=ansible@control\.example\.invalid' "$tmp/capture.env"
grep -Eq 'HOMECLUSTER_K3S_NODE_TARGET_MAP=.*agent-a=ops@agent\.example\.invalid' "$tmp/capture.env"

cat >"$tmp/bin/ansible-inventory" <<'SH'
#!/usr/bin/env bash
exit 2
SH
chmod +x "$tmp/bin/ansible-inventory"

set +e
failure_output="$(
  PATH="$tmp/bin:$PATH" \
  HOMECLUSTER_ANSIBLE_INVENTORY="$tmp/inventory.yml" \
  HOMECLUSTER_K3S_COLLECTOR="$tmp/collector" \
  bash "$status_script" --json
)"
failure_rc=$?
set -e
[ "$failure_rc" -eq 2 ]
printf '%s\n' "$failure_output" | jq -e '
  .status == "unknown" and
  .reason == "target_resolution_failed" and
  .target_resolution == "unresolved" and
  .target_resolution_reason == "ansible_inventory_failed"
' >/dev/null

printf 'status=pass\n'
