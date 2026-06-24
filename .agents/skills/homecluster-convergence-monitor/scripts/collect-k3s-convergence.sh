#!/usr/bin/env bash
set -euo pipefail

VERSION="0.1.0"
CONTROL_SSH="${MONITOR_CONTROL_SSH:-}"
NODE_SSH_LIST="${MONITOR_NODE_SSH_LIST:-}"
EXPECTED_NODES="${MONITOR_EXPECTED_NODES:-4}"
EXPECTED_NODE_EXPORTER="${MONITOR_EXPECTED_NODE_EXPORTER:-$EXPECTED_NODES}"
OBS_NAMESPACE="${MONITOR_OBS_NAMESPACE:-observability-stg}"
NODE_EXPORTER_SELECTOR="${MONITOR_NODE_EXPORTER_SELECTOR:-app.kubernetes.io/name=prometheus-node-exporter}"
TIMEOUT_SECONDS="${MONITOR_TIMEOUT_SECONDS:-20}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$1" >&2
    exit 2
  }
}

json_string() {
  jq -Rn --arg v "$1" '$v'
}

sanitize_output() {
  local output="$1"
  case "$output" in
    *"REMOTE HOST IDENTIFICATION HAS CHANGED"*)
      printf '%s' "ssh_host_key_changed"
      ;;
    *"Host key verification failed"*)
      printf '%s' "ssh_host_key_verification_failed"
      ;;
    *"Permission denied"*)
      printf '%s' "ssh_permission_denied"
      ;;
    *"Could not resolve hostname"*)
      printf '%s' "ssh_name_resolution_failed"
      ;;
    *"Connection timed out"*|*"No route to host"*)
      printf '%s' "ssh_connect_failed"
      ;;
    *)
      printf '%s' "$output"
      ;;
  esac
}

run_local() {
  timeout "$TIMEOUT_SECONDS" bash -lc "$1"
}

run_control() {
  local command_text="$1"
  if [ -n "$CONTROL_SSH" ]; then
    timeout "$TIMEOUT_SECONDS" ssh -oBatchMode=yes -oConnectTimeout=5 "$CONTROL_SSH" \
      "sudo -n k3s kubectl $command_text"
  else
    timeout "$TIMEOUT_SECONDS" kubectl $command_text
  fi
}

capture_control_json() {
  local command_text="$1"
  local output status tmp
  set +e
  output="$(run_control "$command_text" 2>&1)"
  status=$?
  set -e
  output="$(sanitize_output "$output")"
  tmp="$(mktemp)"
  printf '%s' "$output" > "$tmp"
  jq -n --argjson status "$status" --rawfile output "$tmp" \
    '{status:$status, ok:($status == 0), output:$output}'
  rm -f "$tmp"
}

capture_control_text_tail() {
  local command_text="$1"
  local max_lines="$2"
  local output status tmp
  set +e
  if [ -n "$CONTROL_SSH" ]; then
    output="$(timeout "$TIMEOUT_SECONDS" ssh -oBatchMode=yes -oConnectTimeout=5 "$CONTROL_SSH" \
      "sudo -n k3s kubectl $command_text" 2>&1)"
  else
    output="$(timeout "$TIMEOUT_SECONDS" bash -lc "kubectl $command_text" 2>&1)"
  fi
  status=$?
  set -e
  output="$(sanitize_output "$output")"
  output="$(printf '%s\n' "$output" | tail -n "$max_lines")"
  tmp="$(mktemp)"
  printf '%s' "$output" > "$tmp"
  jq -n --argjson status "$status" --rawfile output "$tmp" \
    '{status:$status, ok:($status == 0), output:$output}'
  rm -f "$tmp"
}

capture_ssh_json() {
  local target="$1"
  local command_text="$2"
  local output status tmp
  set +e
  output="$(timeout "$TIMEOUT_SECONDS" ssh -oBatchMode=yes -oConnectTimeout=5 "$target" "$command_text" 2>&1)"
  status=$?
  set -e
  output="$(sanitize_output "$output")"
  tmp="$(mktemp)"
  printf '%s' "$output" > "$tmp"
  jq -n --arg target "$target" --argjson status "$status" --rawfile output "$tmp" \
    '{target:$target, status:$status, ok:($status == 0), output:$output}'
  rm -f "$tmp"
}

require_cmd jq
require_cmd timeout
if [ -n "$CONTROL_SSH$NODE_SSH_LIST" ]; then
  require_cmd ssh
fi

generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

nodes_raw="$(capture_control_json 'get nodes -o json')"
pods_raw="$(capture_control_text_tail 'get pods -A --no-headers -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,NODE:.spec.nodeName,READY:.status.containerStatuses[*].ready' 10000)"
node_exporter_raw="$(capture_control_json "-n ${OBS_NAMESPACE} get ds,pod -l ${NODE_EXPORTER_SELECTOR} -o json")"
events_tail_raw="$(capture_control_text_tail 'get events -A --sort-by=.lastTimestamp --no-headers' 80)"

node_checks_json='[]'
if [ -n "$NODE_SSH_LIST" ]; then
  node_checks_json="$(
    tmp="$(mktemp)"
    for target in $NODE_SSH_LIST; do
      capture_ssh_json "$target" \
        'printf "host="; hostname; printf " uptime="; uptime -p 2>/dev/null || true; printf "\nmount="; findmnt -rn /var/lib/rancher/k3s 2>/dev/null || true; printf "\nrootfs="; df -h / /run /var/lib/rancher/k3s 2>/dev/null || true; printf "\nunits="; systemctl is-active ansible-pull@base.service ansible-pull@k3s_stg_server.service ansible-pull@k3s_stg_agent.service k3s.service k3s-agent.service 2>/dev/null || true' >> "$tmp"
      printf '\n' >> "$tmp"
    done
    jq -s '.' "$tmp"
    rm -f "$tmp"
  )"
fi

jq -n \
  --arg generated_at "$generated_at" \
  --arg version "$VERSION" \
  --arg control_ssh_set "$([ -n "$CONTROL_SSH" ] && printf true || printf false)" \
  --arg node_ssh_count "$(printf '%s\n' "$NODE_SSH_LIST" | awk '{print NF}')" \
  --arg expected_nodes "$EXPECTED_NODES" \
  --arg expected_node_exporter "$EXPECTED_NODE_EXPORTER" \
  --arg obs_namespace "$OBS_NAMESPACE" \
  --arg node_exporter_selector "$NODE_EXPORTER_SELECTOR" \
  --argjson nodes_raw "$nodes_raw" \
  --argjson pods_raw "$pods_raw" \
  --argjson node_exporter_raw "$node_exporter_raw" \
  --argjson events_tail_raw "$events_tail_raw" \
  --argjson node_checks "$node_checks_json" '
  def parse_json($raw):
    if $raw.ok then ($raw.output | fromjson? // null) else null end;

  def split_ws:
    split(" ") | map(select(length > 0));

  def parse_pod_text($raw):
    if $raw.ok then
      {items:(($raw.output // "") | split("\n") | map(select(length > 0)) |
        map(split_ws as $cols | {
          metadata:{namespace:($cols[0] // ""), name:($cols[1] // "")},
          status:{phase:($cols[2] // "Unknown")},
          spec:{nodeName:($cols[3] // "")},
          ready_text:($cols[4] // "")
        }))}
    else null end;

  def pod_ready($pod):
    if ($pod.ready_text? != null) then
      (($pod.ready_text | split(",") | map(select(length > 0 and . != "<none>"))) as $states |
        ($states | map(select(. == "true")) | length) as $ready |
        ($states | length) as $total |
        {ready:$ready,total:$total,text:(($ready|tostring)+"/"+($total|tostring))})
    else
      (($pod.status.containerStatuses // []) | map(select(.ready == true)) | length) as $ready |
      (($pod.status.containerStatuses // []) | length) as $total |
      {ready:$ready,total:$total,text:(($ready|tostring)+"/"+($total|tostring))}
    end;

  (parse_json($nodes_raw)) as $nodes_json |
  (parse_pod_text($pods_raw)) as $pods_json |
  (parse_json($node_exporter_raw)) as $node_exporter_json |
  ($expected_nodes | tonumber) as $expected_nodes_n |
  ($expected_node_exporter | tonumber) as $expected_node_exporter_n |

  ($nodes_json.items // []) as $node_items |
  ($pods_json.items // []) as $pod_items |
  ($node_exporter_json.items // []) as $node_exporter_items |

  ($node_items | map({
    name:.metadata.name,
    ready: ((.status.conditions // []) | map(select(.type=="Ready")) | .[0].status // "Unknown"),
    memory_pressure: ((.status.conditions // []) | map(select(.type=="MemoryPressure")) | .[0].status // "Unknown"),
    disk_pressure: ((.status.conditions // []) | map(select(.type=="DiskPressure")) | .[0].status // "Unknown"),
    pid_pressure: ((.status.conditions // []) | map(select(.type=="PIDPressure")) | .[0].status // "Unknown")
  })) as $nodes |

  ($pod_items | map(select(.status.phase != "Running" and .status.phase != "Succeeded")) |
    map({namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,node:(.spec.nodeName // "")})) as $non_running |

  ($pod_items | map(select(.status.phase == "Running")) |
    map(. as $p | pod_ready($p) as $r |
      select($r.total > 0 and $r.ready != $r.total) |
      {namespace:$p.metadata.namespace,name:$p.metadata.name,ready:$r.text,node:($p.spec.nodeName // "")})) as $running_not_ready |

  ($node_exporter_items | map(select(.kind == "DaemonSet")) | .[0] // {}) as $node_exporter_ds |
  ($node_exporter_items | map(select(.kind == "Pod")) |
    map({namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,node:(.spec.nodeName // ""),ready:(pod_ready(.).text)})) as $node_exporter_pods |
  ($node_exporter_items | map(select(.kind == "Pod" and .status.phase == "Running")) |
    map(pod_ready(.)) |
    map(select(.total > 0 and .ready == .total)) |
    length) as $node_exporter_ready_pod_count |
  ([($node_exporter_ds.status.desiredNumberScheduled // 0), $expected_node_exporter_n] | max) as $node_exporter_desired |

  (($events_tail_raw.output // "") | split("\n") |
    map(select(test("Node password|hash does not match|authorization|DiskPressure|ImagePullBackOff|CrashLoopBackOff|Evicted|NodeNotReady|TaintManagerEviction|Failed|Unhealthy"; "i"))) |
    .[-20:] |
    map({line:.})) as $signals |

  ([
    $nodes_raw.output,
    $pods_raw.output,
    $node_exporter_raw.output,
    $events_tail_raw.output
  ] + ($node_checks | map(.output))) as $diagnostic_outputs |

  ([
    (if ($nodes_raw.ok | not) then "kubernetes_api_unreachable" else empty end),
    (if ($nodes | length) < $expected_nodes_n then "missing_expected_nodes" else empty end),
    (if ($nodes | map(select(.ready != "True")) | length) > 0 then "nodes_not_ready" else empty end),
    (if ($nodes | map(select(.disk_pressure == "True" or .memory_pressure == "True" or .pid_pressure == "True")) | length) > 0 then "node_pressure" else empty end),
    (if ($non_running | length) > 0 then "non_running_pods" else empty end),
    (if ($running_not_ready | length) > 0 then "running_pods_not_ready" else empty end),
    (if $node_exporter_ready_pod_count < $expected_node_exporter_n then "node_exporter_not_ready" else empty end),
    (if ($signals | map(select((.line // "" | test("Node password|hash does not match|authorization"; "i")))) | length) > 0 then "node_identity_signal" else empty end),
    (if ($diagnostic_outputs | map(select((. // "") | test("ssh_host_key_(changed|verification_failed)"))) | length) > 0 then "ssh_host_key_problem" else empty end)
  ]) as $issues |

  {
    generated_at:$generated_at,
    collector:{name:"homecluster-convergence-monitor",version:$version},
    inputs:{
      control_ssh_set:($control_ssh_set == "true"),
      node_ssh_count:($node_ssh_count | tonumber),
      expected_nodes:$expected_nodes_n,
      expected_node_exporter:$expected_node_exporter_n,
      obs_namespace:$obs_namespace,
      node_exporter_selector:$node_exporter_selector
    },
    nodes:{
      api_ok:$nodes_raw.ok,
      count:($nodes | length),
      ready_count:($nodes | map(select(.ready == "True")) | length),
      items:$nodes
    },
    pods:{
      api_ok:$pods_raw.ok,
      total:($pod_items | length),
      non_running:$non_running,
      running_not_ready:$running_not_ready
    },
    node_exporter:{
      api_ok:$node_exporter_raw.ok,
      desired:$node_exporter_desired,
      ready:$node_exporter_ready_pod_count,
      pods:$node_exporter_pods
    },
    node_checks:$node_checks,
    signals:$signals,
    assessment:{
      status:(if ($nodes_raw.ok | not) then "unknown"
        elif ($issues | length) == 0 then "healthy"
        elif ($issues | index("node_pressure")) then "blocked"
        elif (($issues | index("node_identity_signal")) and (($issues | index("missing_expected_nodes")) | not)) then "blocked"
        else "converging" end),
      issues:$issues
    }
  }'
