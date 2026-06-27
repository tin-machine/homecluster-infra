---
status: current
audience: operator
scope: site-input-contract
last_reviewed: 2026-06-08
---

# 外部入力

## 目的

実 inventory、site 値、secret、state、kubeconfig を commit せず、この repository を実 site に対して実行可能な状態に保つ。

## 外部入力の形式

Ansible は外部 `../inventory.yml` entrypoint を使う。

Terraform は Ansible inventory を解析しない。各 Terraform root は root 固有の JSON variable file を受け取る。

```text
terraform/
  common-crds.tfvars.json
  common-addons.tfvars.json
  common-certificates.tfvars.json
  staging.tfvars.json
```

staging root は、chart ごとの site override をすべて含む directory も要求する。

```text
helm/staging/
  values-grafana.yaml
  values-kube-state-metrics.yaml
  values-loki.yaml
  values-mimir.yaml
  values-minio.yaml
  values-node-exporter.yaml
  values-otel-collector.yaml
  values-prometheus.yaml
  values-tempo.yaml
```

`staging.tfvars.json` は `site_values_dir` にこの directory を設定する。Terraform は reusable base values と staging values の後、最後に各 site override を読み込む。

## plan 例

```bash
inputs=/path/to/generated/site-inputs

terraform -chdir=terraform/env/common-crds plan \
  -var-file="$inputs/terraform/common-crds.tfvars.json"

terraform -chdir=terraform/env/staging plan \
  -var-file="$inputs/terraform/staging.tfvars.json"
```

backend state path は operator または controller runtime input として渡す。

## Fail closed 方針

site-specific Terraform variable は documentation value の default を持たない。staging root は、すべての site override file が存在することを要求する。このため input が欠けている場合、example 値を silently apply せず plan が停止する。

staging local registry は site-local な LoadBalancer Service と PVC を作るため、public default では disabled とする。復旧や desktop workload 用 image 配布で必要な場合だけ、private site input で `registry_enabled = true` と registry VIP / node selector / label 値を明示する。

private input workflow は、plan 前に documentation range、example selector、空の required value も拒否するべきである。

## OpenWrt live input

OpenWrt の live apply は外部 inventory を必須入力とする。`ansible_host` は SSH 接続先であり、`/etc/config/network` へ書く LAN address ではない。実機へ反映する場合は、router hostvars に少なくとも次を明示する。

2026-06-07 の OpenWrt LAN address incident では、external inventory に `openwrt_lan_ipaddr` が無いまま `network` path が実行され、public-safe documentation address が router の LAN address として反映された。この種の入力は「未指定なら example 値」ではなく「未指定なら停止」として扱う。

| 変数 | 外部入力 |
| --- | --- |
| `openwrt_lan_ipaddr` | LAN interface に書く実 IP。空値と `192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24` は拒否する |
| `openwrt_lan_netmask` | LAN netmask。実 site の LAN 設計を外部 inventory に置く |
| `openwrt_ula_prefix` | `/etc/config/network` の `globals.ula_prefix` に書く RFC4193 ULA `/48`。実 prefix は外部 inventory に置き、public default や docs に残さない |
| `openwrt_dhcp_ntp_servers` | DHCP Option 42 で配る NTP server。router 自身を配る場合も外部 inventory に明示する |
| `openwrt_gentoo_server_host` | PXE / NFS / TFTP / bootstrap log / token exchange が参照する server IP。LAN IP への暗黙 fallback は使わない |
| `openwrt_enable_storage`、`openwrt_enable_wireless`、`openwrt_enable_frr`、`openwrt_enable_prometheus_exporter`、`openwrt_syslog_remote_enabled`、`openwrt_dnsmasq_log_queries`、`openwrt_banip_install`、`openwrt_banip_enabled` | public default は安全側で disabled。実機で有効にする role だけ外部 inventory で `true` にする。dnsmasq query log は量と privacy impact が大きいため、短時間の logging-only gate で使う。banIP は package install と service enable を分ける |
| `openwrt_firewall_allow_wan_ipv4_ping` | WAN 側 IPv4 echo-request を受ける必要がある site だけ外部 inventory で明示する。public default は disabled |
| `openwrt_firewall_allow_smb` | SMB backup share を使う site だけ外部 inventory で明示する |
| `openwrt_storage_device`、`openwrt_storage_expected_model`、`openwrt_storage_expected_serial`、`openwrt_storage_destructive_confirm` | destructive storage operation の対象 device と確認 material。public default の device 名だけを根拠に repartition / format してはならない |
| `openwrt_storage_protected_mounts` | destructive storage guard で保護する追加 mountpoint。storage role とは別に管理される external share の source / bind target がある場合は、外部 inventory でここへ追加する |
| `openwrt_backup_share_enabled`、`openwrt_backup_share_uuid`、`openwrt_backup_share_source`、`openwrt_backup_share_bind_target`、`openwrt_backup_share_nfs_client_cidr` | router 上の external share を mount / export する site-local 値。public default の UUID empty / documentation CIDR / example path だけで live export を成立させない |
| `openwrt_gentoo_ssh_allow_root_login`、`openwrt_gentoo_ssh_allow_password_auth` | PXE Gentoo rootfs の recovery-only SSH policy。public default は disabled。closed lab で復旧用途が必要な場合だけ外部 inventory で明示 opt-in する |

`openwrt_ntp_servers` は router 自身が参照する upstream NTP server であり、client へ配る `openwrt_dhcp_ntp_servers` とは別である。OpenWrt pool を使うなら省略できるが、site 内 NTP や閉域 NTP を使うなら外部 inventory に明示する。

FRR を有効にする場合は、`openwrt_frr_peer_listen_range` または `openwrt_frr_static_neighbors` を実 network で指定する。router-id は `openwrt_lan_ipaddr` から導出できるが、routing policy として固定したい場合は `openwrt_frr_router_id` を明示する。prefix filter を使う場合は `openwrt_frr_in_prefix_list` と `openwrt_frr_prefix_lists` をセットで定義し、documentation prefix を live policy に残さない。

リモート syslog を有効にする場合は、`openwrt_syslog_remote_host` を外部 inventory に明示する。public default の documentation address を送信先にしてはならない。

dnsmasq query log を使う場合は、`openwrt_dnsmasq_log_queries: true` を外部 inventory で明示する。public default は disabled であり、常時収集の前提にしない。OpenWrt 24.10 の dnsmasq init script では UCI `logqueries` が `--log-queries=extra` に変換されるため、client、query、reply の情報が syslog に出る。private domain、端末名、利用サービスが見えるため、最初は短時間の観測 window、短い retention、低 cardinality label で扱う。

banIP を有効にする場合は、`openwrt_banip_feeds` を外部 inventory で明示する。public default は empty list であり、実機への feed policy を暗黙に選ばない。`luci-app-banip` は `openwrt_banip_install_luci: true` を指定した場合だけ導入する。

現行 staging では、MetalLB BGP VIP pool は site-specific subnet 内の `/32` 広告として OpenWrt FRR が受ける。OpenWrt 側の inbound prefix-list は site BGP VIP subnet の `/32` だけを permit し、その他を deny する。OpenWrt syslog は staging OTel Collector の syslog LoadBalancer へ送る。

`otel_syslog_load_balancer_source_ranges` は optional である。空の場合は Kubernetes Service の source range restriction を使わない。指定する場合は private site input に置き、OpenWrt の syslog 送信元 `/32` など実 site の CIDR を使う。public default や example に実 CIDR を置かない。

`security_lab_default_node_selector_annotation_key` は optional である。public default は `homelab.example.com/default-node-selector` で、annotation value は `workload_node_selector` から生成する。実 site 固有の annotation domain を維持したい場合だけ private site input で上書きする。

OpenWrt role の default policy:

- live system へ書き込まれる site-specific value には meaningful public default を持たせない。
- `enabled: false`、empty list、empty string + assert のような no-op / fail-closed default は許可する。
- IP、IPv6 ULA prefix、subnet、route、BGP policy、syslog destination、storage device、PXE / NFS / TFTP endpoint、recovery-only SSH policy は external inventory か明示的な operator override を正とする。
- `default()` は task / template の各所に散らさず、role 入口で effective value を作って assert する。
- `openwrt_gentoo_server_host | default(openwrt_lan_ipaddr)` のような live endpoint の fallback chain は避ける。

PXE Gentoo official binhost opt-in:

| 変数 | 外部入力 |
| --- | --- |
| `openwrt_gentoo_official_binhost_enabled` | 公式 Gentoo binhost から特定 package を取得する場合だけ外部 inventory で `true` にする。public default は disabled |
| `openwrt_gentoo_official_binhost_packages` | `--getbinpkgonly` で取得する package atom の明示 list。空 list が public default |
| `openwrt_gentoo_official_binhost_uri` | 通常は role default の Gentoo arm64 公式 binhost を使う。mirror / profile を変える場合だけ外部 inventory で上書きする |
| `openwrt_gentoo_official_binhost_emerge_args` | `emerge` に渡す option list。public default は `--getbinpkgonly` と `--binpkg-respect-use=y` を含み、source build fallback を許さない |

この opt-in は `PORTAGE_BINHOST` を該当 emerge の環境変数として一時上書きする。通常の Portage run を global に `getbinpkg` 化せず、公式 binhost に無い場合は source build へ落ちずに fail-closed する。shell に渡す `name` / `uri` / `location` / `emerge_args` / `packages` は role 側で文字種を検証する。

## ARM64 host role live input

ARM64 host role でも、network exposure を変える値は外部 inventory を正とする。

| 変数 | 外部入力 |
| --- | --- |
| `distcc.enabled` | distcc を有効化する host だけ外部 inventory で `true` にする。public default は disabled |
| `distcc.allow`、`distcc_default_allow` | distcc daemon の allowlist。public default は empty list とし、実 subnet / host range は外部 inventory に置く |

`distcc_default_allow` は compatibility 用の default hook として残せるが、public repository で meaningful CIDR を持たせない。site-wide default を使う場合も private inventory 側で定義する。

## 境界

- generated tfvars、site values、kubeconfig、plan file、state はこの repository の外に置く。state と plan は sensitive variable の表示抑止だけでは secret 保持を防げないため、private secret material と同等に扱う。
- secret は Helm values に入れない。sensitive Terraform variable と Kubernetes Secret 経由で注入する。Kubernetes provider が write-only attribute を持つ Secret data は `data_wo` と revision marker を使い、rotation 時は revision を増やす。
- public CI は実入力なしで source を validate し、live plan / apply は実行しない。
- private operator workflow は、review 済み public commit SHA と commit 済み private input revision を 1 つの revision-pinned bundle に束ねてから live validation を実行する。
