#!/usr/bin/env bash
set -euo pipefail

VERSION="0.1.0"
ROUTER_SSH="${OPENWRT_CHECK_ROUTER_SSH:-}"
EXPECTED_RELEASE="${OPENWRT_CHECK_EXPECTED_RELEASE:-}"
REQUIRED_PACKAGES="${OPENWRT_CHECK_REQUIRED_PACKAGES:-}"
REQUIRED_SERVICES="${OPENWRT_CHECK_REQUIRED_SERVICES:-}"
EXPECTED_MOUNTS="${OPENWRT_CHECK_EXPECTED_MOUNTS:-}"
ROUTE_TARGETS="${OPENWRT_CHECK_ROUTE_TARGETS:-}"
ROUTE_REJECT_DEVS="${OPENWRT_CHECK_ROUTE_REJECT_DEVS:-pppoe-wan wan}"
K3S_CONTROL_SSH="${OPENWRT_CHECK_K3S_CONTROL_SSH:-}"
OBS_NAMESPACE="${OPENWRT_CHECK_OBS_NAMESPACE:-observability-stg}"
TIMEOUT_SECONDS="${OPENWRT_CHECK_TIMEOUT_SECONDS:-20}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$1" >&2
    exit 2
  }
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
    *"Connection timed out"*|*"No route to host"*|*"Connection refused"*)
      printf '%s' "ssh_connect_failed"
      ;;
    *)
      printf '%s' "$output"
      ;;
  esac
}

capture_json() {
  local output="$1"
  local status="$2"
  local tmp
  output="$(sanitize_output "$output")"
  tmp="$(mktemp)"
  printf '%s' "$output" > "$tmp"
  jq -n --argjson status "$status" --rawfile output "$tmp" \
    '{status:$status, ok:($status == 0), output:$output}'
  rm -f "$tmp"
}

run_router() {
  local command_text="$1"
  local output status
  if [ -z "$ROUTER_SSH" ]; then
    capture_json "OPENWRT_CHECK_ROUTER_SSH is not set" 64
    return
  fi
  set +e
  output="$(timeout "$TIMEOUT_SECONDS" ssh -oBatchMode=yes -oConnectTimeout=5 -oLogLevel=ERROR "$ROUTER_SSH" "$command_text" 2>&1)"
  status=$?
  set -e
  capture_json "$output" "$status"
}

run_k3s() {
  local command_text="$1"
  local output status
  if [ -z "$K3S_CONTROL_SSH" ]; then
    capture_json "OPENWRT_CHECK_K3S_CONTROL_SSH is not set" 64
    return
  fi
  set +e
  output="$(timeout "$TIMEOUT_SECONDS" ssh -oBatchMode=yes -oConnectTimeout=5 -oLogLevel=ERROR "$K3S_CONTROL_SSH" \
    "sudo -n k3s kubectl $command_text" 2>&1)"
  status=$?
  set -e
  capture_json "$output" "$status"
}

remote_word_list() {
  local words="$1"
  local prefix="$2"
  local command_text=""
  local item
  for item in $words; do
    command_text="${command_text}printf '%s %s ' '${prefix}' '${item}'; "
    command_text="${command_text}"
  done
  printf '%s' "$command_text"
}

require_cmd jq
require_cmd timeout
require_cmd ssh

generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

board_raw="$(run_router 'ubus call system board')"
release_raw="$(run_router 'cat /etc/openwrt_release')"
pkg_mgr_raw="$(run_router 'for c in opkg apk; do command -v "$c" >/dev/null 2>&1 && echo "$c"; done; true')"
packages_raw="$(run_router 'if command -v opkg >/dev/null 2>&1; then opkg list-installed | cut -d" " -f1; elif command -v apk >/dev/null 2>&1; then apk info; else true; fi')"
services_raw="$(run_router 'for s in network firewall dnsmasq rpcbind nfsd frr banip prometheus-node-exporter-lua uhttpd log; do printf "SERVICE %s " "$s"; if [ ! -x "/etc/init.d/$s" ]; then printf "missing"; elif [ "$s" = frr ]; then if pgrep -f "/usr/sbin/(watchfrr|zebra|bgpd|staticd)" >/dev/null 2>&1; then printf "running"; else printf "present_not_running"; fi; else /etc/init.d/$s status 2>&1 | head -n 3 | tr "\n" " "; fi; printf "\n"; done')"
mounts_raw="$(run_router 'mount')"
df_raw="$(run_router 'df -h')"
fstab_raw="$(run_router 'uci -q show fstab 2>/dev/null | sed -E "s/(uuid=).*/\1<redacted>/; s/(password=).*/\1<redacted>/; s/(key=).*/\1<redacted>/" || true')"
fw_raw="$(run_router 'if command -v fw4 >/dev/null 2>&1; then fw4 check; else echo fw4_missing; fi')"
pxe_raw="$(run_router 'printf "enable_tftp="; uci -q get dhcp.@dnsmasq[0].enable_tftp 2>/dev/null || true; printf "\ntftp_root="; uci -q get dhcp.@dnsmasq[0].tftp_root 2>/dev/null || true; printf "\nexportfs="; if command -v exportfs >/dev/null 2>&1; then exportfs -v 2>/dev/null | wc -l; else echo missing; fi')"
bgp_raw="$(run_router 'if command -v vtysh >/dev/null 2>&1; then vtysh -c "show bgp summary" 2>&1 | sed -n "1,120p"; else echo vtysh_missing; fi')"

route_raw="$(
  tmp="$(mktemp)"
  for target in $ROUTE_TARGETS; do
    run_router "ip route get '$target' 2>&1 | head -n 1" >> "$tmp"
    printf '\n' >> "$tmp"
  done
  jq -s '.' "$tmp"
  rm -f "$tmp"
)"

k3s_nodes_raw="$(run_k3s 'get nodes -o json')"
k3s_pods_raw="$(run_k3s 'get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded --no-headers -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,NODE:.spec.nodeName')"
k3s_obs_raw="$(run_k3s "-n ${OBS_NAMESPACE} get svc,endpoints,pods,ds,deploy,sts -o json")"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
printf '%s' "$board_raw" > "$tmpdir/board_raw.json"
printf '%s' "$release_raw" > "$tmpdir/release_raw.json"
printf '%s' "$pkg_mgr_raw" > "$tmpdir/pkg_mgr_raw.json"
printf '%s' "$packages_raw" > "$tmpdir/packages_raw.json"
printf '%s' "$services_raw" > "$tmpdir/services_raw.json"
printf '%s' "$mounts_raw" > "$tmpdir/mounts_raw.json"
printf '%s' "$df_raw" > "$tmpdir/df_raw.json"
printf '%s' "$fstab_raw" > "$tmpdir/fstab_raw.json"
printf '%s' "$fw_raw" > "$tmpdir/fw_raw.json"
printf '%s' "$pxe_raw" > "$tmpdir/pxe_raw.json"
printf '%s' "$bgp_raw" > "$tmpdir/bgp_raw.json"
printf '%s' "$route_raw" > "$tmpdir/route_raw.json"
printf '%s' "$k3s_nodes_raw" > "$tmpdir/k3s_nodes_raw.json"
printf '%s' "$k3s_pods_raw" > "$tmpdir/k3s_pods_raw.json"
printf '%s' "$k3s_obs_raw" > "$tmpdir/k3s_obs_raw.json"

jq -n \
  --arg generated_at "$generated_at" \
  --arg version "$VERSION" \
  --arg expected_release "$EXPECTED_RELEASE" \
  --arg required_packages "$REQUIRED_PACKAGES" \
  --arg required_services "$REQUIRED_SERVICES" \
  --arg expected_mounts "$EXPECTED_MOUNTS" \
  --arg route_targets "$ROUTE_TARGETS" \
  --arg route_reject_devs "$ROUTE_REJECT_DEVS" \
  --arg router_ssh_set "$([ -n "$ROUTER_SSH" ] && printf true || printf false)" \
  --arg k3s_control_ssh_set "$([ -n "$K3S_CONTROL_SSH" ] && printf true || printf false)" \
  --arg obs_namespace "$OBS_NAMESPACE" \
  --slurpfile board_raw_file "$tmpdir/board_raw.json" \
  --slurpfile release_raw_file "$tmpdir/release_raw.json" \
  --slurpfile pkg_mgr_raw_file "$tmpdir/pkg_mgr_raw.json" \
  --slurpfile packages_raw_file "$tmpdir/packages_raw.json" \
  --slurpfile services_raw_file "$tmpdir/services_raw.json" \
  --slurpfile mounts_raw_file "$tmpdir/mounts_raw.json" \
  --slurpfile df_raw_file "$tmpdir/df_raw.json" \
  --slurpfile fstab_raw_file "$tmpdir/fstab_raw.json" \
  --slurpfile fw_raw_file "$tmpdir/fw_raw.json" \
  --slurpfile pxe_raw_file "$tmpdir/pxe_raw.json" \
  --slurpfile bgp_raw_file "$tmpdir/bgp_raw.json" \
  --slurpfile route_raw_file "$tmpdir/route_raw.json" \
  --slurpfile k3s_nodes_raw_file "$tmpdir/k3s_nodes_raw.json" \
  --slurpfile k3s_pods_raw_file "$tmpdir/k3s_pods_raw.json" \
  --slurpfile k3s_obs_raw_file "$tmpdir/k3s_obs_raw.json" '
  ($board_raw_file[0]) as $board_raw |
  ($release_raw_file[0]) as $release_raw |
  ($pkg_mgr_raw_file[0]) as $pkg_mgr_raw |
  ($packages_raw_file[0]) as $packages_raw |
  ($services_raw_file[0]) as $services_raw |
  ($mounts_raw_file[0]) as $mounts_raw |
  ($df_raw_file[0]) as $df_raw |
  ($fstab_raw_file[0]) as $fstab_raw |
  ($fw_raw_file[0]) as $fw_raw |
  ($pxe_raw_file[0]) as $pxe_raw |
  ($bgp_raw_file[0]) as $bgp_raw |
  ($route_raw_file[0]) as $route_raw |
  ($k3s_nodes_raw_file[0]) as $k3s_nodes_raw |
  ($k3s_pods_raw_file[0]) as $k3s_pods_raw |
  ($k3s_obs_raw_file[0]) as $k3s_obs_raw |

  def words($s): ($s | split(" ") | map(select(length > 0)));
  def lines($s): (($s // "") | split("\n") | map(select(length > 0)));
  def parse_json($raw): if $raw.ok then ($raw.output | fromjson? // null) else null end;
  def release_value($text):
    (lines($text) | map(select(startswith("DISTRIB_RELEASE="))) | .[0]? // "" |
      sub("^DISTRIB_RELEASE="; "") | .[1:-1]);
  def service_items($text):
    lines($text) | map(capture("^SERVICE (?<name>[^ ]+) (?<status>.*)$")? // empty |
      . + {
        present:((.status | test("missing")) | not),
        active:(.status | test("running|active"; "i")),
        missing:(.status | test("missing|not found"; "i"))
      });
  def mounted($target; $mount_text):
    ($mount_text | test(" on " + ($target | gsub("([][(){}.+*?^$\\\\|])"; "\\\\\\1")) + " "));
  def route_item($raw):
    {
      ok:$raw.ok,
      output:($raw.output // ""),
      rejected_dev:(words($route_reject_devs) as $rejects |
        any($rejects[]; . as $dev |
          ($raw.output // "" | test(" dev " + ($dev | gsub("([][(){}.+*?^$\\\\|])"; "\\\\\\1")) + "( |$)"))))
    };
  def pod_text_items($text):
    lines($text) | map(split(" ") | map(select(length > 0)) |
      {namespace:(.[0] // ""), name:(.[1] // ""), phase:(.[2] // ""), node:(.[3] // "")});

  (parse_json($board_raw)) as $board |
  (release_value($release_raw.output // "")) as $release |
  (words($required_packages)) as $required_pkg_list |
  (lines($packages_raw.output // "")) as $installed_pkg_list |
  (words($required_services)) as $required_svc_list |
  (service_items($services_raw.output // "")) as $service_list |
  (words($expected_mounts)) as $expected_mount_list |
  ($route_raw | map(route_item(.))) as $routes |
  (parse_json($k3s_nodes_raw)) as $k3s_nodes |
  (parse_json($k3s_obs_raw)) as $k3s_obs |

  ($required_pkg_list | map(select(. as $p | ($installed_pkg_list | index($p) | not)))) as $missing_packages |
  ($required_svc_list | map(. as $s |
    ($service_list | map(select(.name == $s)) | .[0] // {name:$s,present:false,active:false,missing:true,status:"missing"})) |
    map(select((.present | not) or (.active | not)))) as $bad_services |
  ($expected_mount_list | map(select(mounted(.; $mounts_raw.output // "") | not))) as $missing_mounts |
  ($routes | map(select(.rejected_dev))) as $rejected_routes |

  ($k3s_nodes.items // []) as $k3s_node_items |
  ($k3s_node_items | map({
    name:.metadata.name,
    ready:((.status.conditions // []) | map(select(.type=="Ready")) | .[0].status // "Unknown")
  })) as $k3s_nodes_summary |
  (pod_text_items($k3s_pods_raw.output // "")) as $non_running_pods |

  ([
    (if ($router_ssh_set != "true") then "router_ssh_not_configured" else empty end),
    (if ($board_raw.ok | not) then "router_ssh_unreachable" else empty end),
    (if ($expected_release | length > 0 and $release != $expected_release) then "release_mismatch" else empty end),
    (if ($missing_packages | length) > 0 then "missing_required_packages" else empty end),
    (if ($bad_services | length) > 0 then "missing_required_services" else empty end),
    (if ($missing_mounts | length) > 0 then "expected_mount_missing" else empty end),
    (if ($rejected_routes | length) > 0 then "route_target_rejected_dev" else empty end),
    (if (($route_targets | length) > 0) and (($bgp_raw.output // "") | test("vtysh_missing|not found"; "i")) then "bgp_unavailable" else empty end),
    (if (($k3s_control_ssh_set == "true") and ($k3s_nodes_raw.ok | not)) then "k3s_unreachable" else empty end),
    (if ([$board_raw.output,$release_raw.output,$k3s_nodes_raw.output] | map(select((. // "") | test("ssh_host_key_(changed|verification_failed)"))) | length) > 0 then "ssh_host_key_problem" else empty end)
  ]) as $issues |

  {
    generated_at:$generated_at,
    collector:{name:"homecluster-openwrt-postupgrade-check",version:$version},
    inputs:{
      router_ssh_set:($router_ssh_set == "true"),
      expected_release:$expected_release,
      required_packages:$required_pkg_list,
      required_services:$required_svc_list,
      expected_mounts:$expected_mount_list,
      route_targets:words($route_targets),
      route_reject_devs:words($route_reject_devs),
      k3s_control_ssh_set:($k3s_control_ssh_set == "true"),
      obs_namespace:$obs_namespace
    },
    router:{
      ssh_ok:$board_raw.ok,
      board:{model:($board.model // null), board_name:($board.board_name // null), kernel:($board.kernel // null)},
      release:{ok:($release_raw.ok), current:$release, expected:$expected_release, matches:(($expected_release | length == 0) or $release == $expected_release)},
      package_manager:{ok:$pkg_mgr_raw.ok, commands:lines($pkg_mgr_raw.output // "")},
      packages:{ok:$packages_raw.ok, installed_count:($installed_pkg_list | length), missing_required:$missing_packages},
      services:{ok:$services_raw.ok, items:$service_list, not_ready:$bad_services},
      storage:{mount_output_ok:$mounts_raw.ok, df_ok:$df_raw.ok, expected_missing:$missing_mounts, df_excerpt:lines($df_raw.output // "")},
      fstab:{ok:$fstab_raw.ok, excerpt:lines($fstab_raw.output // "")},
      firewall:{ok:$fw_raw.ok, output:($fw_raw.output // "")},
      pxe_nfs:{ok:$pxe_raw.ok, output:($pxe_raw.output // "")},
      bgp:{ok:$bgp_raw.ok, output:($bgp_raw.output // "")},
      routes:{items:$routes}
    },
    k3s:{
      configured:($k3s_control_ssh_set == "true"),
      nodes:{ok:$k3s_nodes_raw.ok, count:($k3s_nodes_summary | length), items:$k3s_nodes_summary},
      non_running_pods:{ok:$k3s_pods_raw.ok, items:$non_running_pods},
      observability:{ok:$k3s_obs_raw.ok, item_count:(($k3s_obs.items // []) | length)}
    },
    signals:{
      ssh_host_key_problem:(($issues | index("ssh_host_key_problem")) != null)
    },
    assessment:{
      status:(if (($issues | index("router_ssh_not_configured")) != null) or (($issues | index("router_ssh_unreachable")) != null) then "unknown"
        elif ($issues | length) == 0 then "healthy"
        elif (($issues | index("missing_required_packages")) != null)
          or (($issues | index("missing_required_services")) != null)
          or (($issues | index("expected_mount_missing")) != null)
          or (($issues | index("route_target_rejected_dev")) != null)
          or (($issues | index("k3s_unreachable")) != null) then "blocked"
        else "degraded" end),
      phase:(if (($issues | index("router_ssh_not_configured")) != null) or (($issues | index("router_ssh_unreachable")) != null) then "router-ssh"
        elif (($issues | index("release_mismatch")) != null) then "release"
        elif (($issues | index("missing_required_packages")) != null) then "package-restore"
        elif (($issues | index("missing_required_services")) != null) then "service-restore"
        elif (($issues | index("expected_mount_missing")) != null) then "storage"
        elif (($issues | index("route_target_rejected_dev")) != null) or (($issues | index("bgp_unavailable")) != null) then "routing"
        elif (($issues | index("k3s_unreachable")) != null) then "k3s"
        else "steady-state" end),
      issues:$issues
    }
  }'
