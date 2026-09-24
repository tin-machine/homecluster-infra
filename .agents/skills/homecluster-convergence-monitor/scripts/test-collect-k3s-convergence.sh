#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
collector="$script_dir/collect-k3s-convergence.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat >"$tmp/bin/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
command_text="${*: -1}"
printf '%s\n' 'Could not chdir to home directory /example/home: No such file or directory' >&2
printf '%s\n' 'bash: warning: setlocale: LC_ALL: cannot change locale' >&2
case "$command_text" in
  *'get nodes -o json'*)
    if [ "${INVALID_NODES:-0}" = "1" ]; then
      printf 'not-json\n'
    else
      cat <<'JSON'
{"items":[{"metadata":{"name":"node-a"},"status":{"conditions":[{"type":"Ready","status":"True"},{"type":"MemoryPressure","status":"False"},{"type":"DiskPressure","status":"False"},{"type":"PIDPressure","status":"False"}]}}]}
JSON
    fi
    ;;
  *'get ds,pod'*)
    printf '{"items":[]}\n'
    ;;
  *'get pods -A'*)
    ;;
  *'get events -A'*)
    ;;
  *)
    printf 'unexpected fake ssh command: %s\n' "$command_text" >&2
    exit 2
    ;;
esac
SH
chmod +x "$tmp/bin/ssh"

healthy="$(
  PATH="$tmp/bin:$PATH" \
  MONITOR_CONTROL_SSH=control-a \
  MONITOR_EXPECTED_NODES=1 \
  MONITOR_EXPECTED_NODE_EXPORTER=0 \
  MONITOR_NODE_SSH_LIST= \
  bash "$collector"
)"

printf '%s\n' "$healthy" | jq -e '
  .assessment.status == "healthy" and
  .nodes.api_ok == true and
  .nodes.count == 1 and
  .nodes.ready_count == 1 and
  (.control_stderr.nodes | contains("Could not chdir")) and
  (.assessment.issues | length) == 0
' >/dev/null

invalid="$(
  PATH="$tmp/bin:$PATH" \
  INVALID_NODES=1 \
  MONITOR_CONTROL_SSH=control-a \
  MONITOR_EXPECTED_NODES=1 \
  MONITOR_EXPECTED_NODE_EXPORTER=0 \
  MONITOR_NODE_SSH_LIST= \
  bash "$collector"
)"

printf '%s\n' "$invalid" | jq -e '
  .assessment.status == "unknown" and
  .nodes.api_ok == false and
  .nodes.count == 0 and
  (.assessment.issues | index("kubernetes_api_invalid_json") != null)
' >/dev/null

printf 'status=pass\n'
