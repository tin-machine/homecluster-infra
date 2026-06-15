---
status: current
audience: operator
scope: openwrt-live-apply-plan
last_reviewed: 2026-06-07
---

# OpenWrt live apply plan

## 目的

OpenWrt の tagless apply を避け、role tag ごとに failure domain を分けて反映する。

この計画は実行承認前の手順書であり、この文書を更新しても実機には反映しない。

## Site input

router inventory host の live input は external inventory を正とする。

必須値:

- `openwrt_lan_ipaddr`: router LAN address
- `openwrt_ula_prefix`: router global ULA `/48` prefix。値は表示せず、存在と形式だけを確認する
- `openwrt_dhcp_ntp_servers`: DHCP Option 42 で配る NTP server
- `openwrt_gentoo_server_host`: PXE / NFS / TFTP / bootstrap endpoint
- `openwrt_gentoo_ansible_pull_repo_url`: PXE client が `ansible-pull` で消費する実行 repo。現行運用では private `homecluster-infra` を正本にする。
- `openwrt_gentoo_ansible_pull_netrc_entries`: private repo を読む read-only credential。値は出力しない。
- `openwrt_frr_peer_listen_range`: MetalLB speaker node を含む実 LAN range
- `openwrt_frr_static_neighbors`: 必要に応じた MetalLB speaker node の実 IP
- `openwrt_frr_in_prefix_list`: `PL-METALLB-IN`
- `openwrt_frr_prefix_lists`: site BGP VIP subnet の `/32` 広告だけを permit し、残りを deny
- `openwrt_syslog_remote_host`: staging OTel syslog LoadBalancer VIP
- `openwrt_syslog_remote_port`: staging OTel syslog TCP port
- `openwrt_syslog_remote_proto`: `tcp`

FRR prefix-list は MetalLB BGP pool の start-end range を完全には表現しない。MetalLB 側は `aggregationLength = 32` で VIP を `/32` として広告するため、OpenWrt 側は site BGP VIP subnet に含まれる `/32` だけを許可する。

2026-06-07 incident note:

- external inventory に `openwrt_lan_ipaddr` が無い状態で `network` path が到達し、documentation address が live router の LAN address として反映された。
- `ansible_host` は SSH 接続先であり、LAN interface へ書く desired address ではない。事故後の現在 IP へ一時接続する場合も、external inventory の desired value は戻すべき LAN address のままにする。
- site-specific / live-impacting input には meaningful default を置かない。no-op default、empty value + assert、`enabled: false` は許可するが、IP / subnet / route / BGP policy / syslog destination / PXE endpoint の fallback chain は禁止する。
- PXE client の `ansible-pull` repo URL と credential は rootfs 内の systemd unit / `/root/.netrc` に baked される。external inventory を直しただけでは active rootfs は変わらないため、`pxe_ansible_pull` gate で unit、credential、vars を再配置する。

## Preflight

実行前に次を確認する。すべて read-only とする。

```bash
ansible-inventory -i /path/to/site-inventory.yml --host router.example \
  | jq '{openwrt_lan_ipaddr, openwrt_ula_prefix_valid: ((.openwrt_ula_prefix // "") | test("^f[cd][0-9a-fA-F]{2}:[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}::/48$")), openwrt_dhcp_ntp_servers, openwrt_gentoo_server_host, openwrt_gentoo_ansible_pull_repo_url, openwrt_frr_in_prefix_list, openwrt_frr_prefix_lists, openwrt_syslog_remote_host, openwrt_syslog_remote_port, openwrt_syslog_remote_proto, openwrt_dnsmasq_log_queries}'

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --syntax-check

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --list-hosts

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --tags network,dhcp,openwrt_frr,openwrt_syslog_remote,firewall,openwrt_banip --list-tasks
```

`--check` は read-only gate として扱わない。OpenWrt role には command / shell / package task があり、check mode でも実機状態へ触れる path を持ち得る。

## Rollback / recovery gate

`network` tag は management address を変更し得るため、apply 承認前に次を確認する。

- 現在の router management IP へ SSH できること。target `openwrt_lan_ipaddr` と現在 IP が違う場合は、host key fingerprint が既知 router と一致することを確認し、必要なら `HostKeyAlias` で接続する。unknown host key を対話で受け入れて進めない。
- rollback apply を Ansible で行う場合は、external inventory の恒久値を書き換えず、現在 IP / management user / SSH option を一時 override する。事故状態の IP を public inventory や repository default に固定しない。
- `sysupgrade -b` と fetch による router config backup は rollback point として有効だが、backup 作成は router へ書き込むため read-only preflight には含めない。承認後、apply 前に private storage へ取得する。backup tarball、raw config、credential は repository に入れない。
- in-band rollback は `/etc/config/network` を元の LAN address / netmask へ戻し、network service reload で反映する。SSH は切断される前提で、operator host の DHCP renew または一時 static address 変更まで手順に含める。
- OpenWrt の NAND recovery / NOR recovery / initramfs boot が使えることを確認する。UART を recovery path として扱う場合は、router の serial device が powered-on host の `/dev/serial/by-id/` に見えていることまで確認する。PXE boot test 用の Pi UART helper が存在するだけでは、router の out-of-band recovery 確認にはならない。

rollback / recovery gate が未完了なら、`network` tag を apply しない。

Ansible で現在 IP へ一時接続する場合の形:

```bash
ansible-playbook -i /path/to/site-inventory.yml ansible/openwrt/site.yml \
  -l router.example --tags network \
  -e ansible_host=<current-router-management-ip> \
  -e ansible_user=root \
  -e 'ansible_ssh_common_args=-oHostKeyAlias=<known-router-host-key-alias> -oBatchMode=yes -oConnectTimeout=3 -oServerAliveInterval=3 -oServerAliveCountMax=1'
```

## Apply order

承認後も tagless apply はしない。次の順序で、各段階の post-check が通ったら次へ進む。

0. `bootstrap`
   - 目的: OpenWrt target に Python 実行環境が無い場合だけ、Ansible module 実行の前提を作る。
   - 注意: `opkg update` / `opkg install` に到達し得るため tagless apply には含めない。既に Python がある既存 router では通常は再実行しない。
   - post-check: 通常 inventory で `ansible router.example -m ansible.builtin.raw -a 'python3 --version'` が通ることを確認する。

1. `network`
   - 目的: LAN IP / PPPoE / bridge 設定の外部入力を確認する。
   - 注意: router LAN address に触るため、直前に rollback 手順と out-of-band recovery を確認する。
2. `dhcp`
   - 目的: DHCP Option 42 と PXE host / boot 定義を反映する。
   - post-check: `dnsmasq --test` と `/tmp/dnsmasq.conf` の `dhcp-option=42` / PXE entry を確認する。`openwrt_dnsmasq_log_queries=true` の短時間 gate では、`uci -q get dhcp.@dnsmasq[0].logqueries` と `/var/etc/dnsmasq.conf.*` の `log-queries=extra`、Loki 側の dnsmasq query log 流入量も確認する。
3. `openwrt_frr`
   - 目的: MetalLB BGP speaker から site BGP VIP subnet 内の `/32` VIP だけを受ける。
   - post-check: FRR の BGP session、prefix-list、site BGP VIP route を確認する。
4. `openwrt_syslog_remote`
   - 目的: OpenWrt syslog を staging OTel syslog LoadBalancer へ送る。
   - post-check: OpenWrt 側 `uci show system` と OTel/Loki 側の router log ingestion を確認する。
5. `firewall`
   - 目的: network / DHCP / FRR / syslog 後の通信 policy を確認する。
   - post-check: FRR TCP/179、syslog TCP/6514、DHCP/DNS/TFTP/NFS の意図した到達性だけを確認する。
6. `openwrt_banip`
   - 目的: firewall4 上に banIP の nftables set を追加し、既知悪性 IP / scan を軽量に減らす。
   - 注意: `opkg install` と外部 feed download に到達し得るため、install と `ban_enabled=1` は分ける。初回は `luci-app-banip` を入れない。
   - post-check: `fw4 check`、`/etc/init.d/banip status`、`nft list table inet banIP`、`logread -e 'banIP/'`、Loki `app="openwrt-router"` を確認する。

Storage、rootfs、TFTP artifact、sysupgrade はこの計画から除外する。必要な場合は別承認と別 rollback point を持つ。
`ansible/openwrt/site.yml` では、これらの入口になる `bootstrap_python`、`openwrt_storage`、`openwrt_gentoo_rootfs`、`openwrt_sysupgrade` に `never` tag を付ける。tagless apply では実行せず、明示 tag 指定時だけ gate に入る。

## Separate gates

### Storage gate

対象 tag:

```bash
--tags openwrt_storage
```

事前確認:

```bash
ansible-inventory -i /path/to/site-inventory.yml --host router.example \
  | jq '{openwrt_storage_device, openwrt_storage_expected_model, openwrt_storage_expected_serial, openwrt_storage_destructive_confirm, openwrt_storage_partition_map, openwrt_storage_manage_fstab, openwrt_storage_force_repartition, openwrt_storage_force_format, openwrt_storage_apply_mounts}'

ssh root@router.example 'block info /dev/sd* /dev/mmcblk* /dev/nvme*n* 2>/dev/null || true; mount | grep -E " on (/srv|/srv/boot|/mnt/external-disk|/srv/external-disk)( |$)" || true; df -h /srv /srv/boot /mnt/external-disk /srv/external-disk 2>/dev/null || true; cat /proc/swaps'

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --tags openwrt_storage --list-tasks
```

stop 条件:

- `openwrt_storage_force_repartition` または `openwrt_storage_force_format` が `true` で、対象 block device / partition が目視確認されていない。
- 破壊的操作なのに `openwrt_storage_destructive_confirm=erase-<device>`、`openwrt_storage_expected_model`、`openwrt_storage_expected_serial` が揃っていない。
- `/srv` 配下に rootfs、TFTP、NFS export、backup などの live data があり、rollback copy が無い。
- `/dev/sdX` の列挙順だけを根拠に対象 device を決めている。
- `/srv`、`/srv/boot`、`/mnt/external-disk`、`/srv/external-disk` の mount source と destructive target が重なっている。

post-check:

```bash
ssh root@router.example 'block info /dev/sd* /dev/mmcblk* /dev/nvme*n* 2>/dev/null || true; uci show fstab; mount | grep -E " on (/srv|/srv/boot|/mnt/external-disk|/srv/external-disk)( |$)"; cat /proc/swaps'
```

### Rootfs / TFTP artifact gate

対象 tag は目的ごとに分ける。広い `pxe` tag を使う場合も、実行前に `--list-tasks` を必ず確認する。

```bash
--tags pxe_ansible_pull
--tags pxe_rootfs
--tags tftp_artifacts,tftp_hosts
--tags pxe_tftp_switch
--tags pxe_rootfs_prune
```

`pxe_ansible_pull` は、active rootfs 内の systemd unit、wrapper、group vars、client vars、timer/service dependencies を更新する gate である。rootfs build や TFTP switch と同じ承認に混ぜず、repo URL や role vars の修正だけを active rootfs に戻す用途ではこの tag だけを先に使う。

事前確認:

```bash
ansible-inventory -i /path/to/site-inventory.yml --host router.example \
  | jq '{openwrt_gentoo_server_host, openwrt_gentoo_ansible_pull_repo_url, ansible_pull_netrc_entry_count: (.openwrt_gentoo_ansible_pull_netrc_entries // [] | length), openwrt_gentoo_release, openwrt_gentoo_release_bundle_enabled, openwrt_gentoo_release_bundle_stage_dates}'

ssh root@router.example 'df -h /srv /srv/gentoo 2>/dev/null || true; find /srv/gentoo/tftp-root -maxdepth 2 -type f -name cmdline.txt -print | sort | head -50; exportfs -v 2>/dev/null || true; cat /tmp/dhcp.leases'

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --tags pxe_rootfs --list-tasks
```

stop 条件:

- `cmdline.txt`、NFS root、TFTP serveraddress、bootstrap server に documentation range が残る。
- active rootfs の `ansible-pull@.service` が、意図した実行 repo 以外を参照する。
- repo が private なのに active rootfs の `/root/.netrc` が存在しない、または `ansible-pull` 用 credential の権限が足りない。
- staging control-plane server role vars に `stage`、`k3s_server.node-ip`、`k3s_server.node-name`、`k3s_server.snapshotter`、`k3s_server.data-dir` が無い。
- 起動中 PXE client が参照中の rootfs / TFTP release を差し替える計画になっている。
- rollback する rootfs release / TFTP artifact / manifest が特定できない。
- chroot package install、dracut、rootfs rebuild、TFTP switch が同じ run に混ざっているが、失敗時の切り戻し境界が決まっていない。

post-check:

```bash
ssh root@router.example 'for config in /var/etc/dnsmasq.conf.*; do dnsmasq --test -C "$config"; grep -E "dhcp-boot|dhcp-option=.*224" "$config"; done; find /srv/gentoo/tftp-root -maxdepth 2 -type f -name cmdline.txt -print | sort | head -50'
```

### Sysupgrade gate

対象 tag:

```bash
--tags sysupgrade
```

detect-only 確認では `openwrt_perform_upgrade=false` を明示する。

```bash
ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --tags sysupgrade \
  -e openwrt_perform_upgrade=false
```

upgrade を行う場合は、直前に private backup と recovery path を確認してから `openwrt_perform_upgrade=true` で実行する。

```bash
ssh root@router.example 'cat /etc/openwrt_release; sysupgrade -l | sort'

ansible-playbook -i /path/to/site-inventory.yml \
  ansible/openwrt/site.yml -l router.example --tags sysupgrade \
  -e openwrt_perform_upgrade=true
```

stop 条件:

- router config backup を private storage へ取得していない。
- OpenWrt recovery、serial console、または物理 access のいずれも確認できない。
- target release / image URL / checksum が review されていない。
- upgrade 後の management IP、host key continuity、DHCP renew 手順が未確認。

post-check:

```bash
ssh root@router.example 'cat /etc/openwrt_release; uci -q get network.lan.ipaddr; /etc/init.d/dnsmasq status; /etc/init.d/firewall status'
ansible -i /path/to/site-inventory.yml router.example -m ansible.builtin.raw -a true
```

## Stop conditions

- `openwrt_lan_ipaddr`、`openwrt_dhcp_ntp_servers`、`openwrt_gentoo_server_host`、`openwrt_syslog_remote_host` に documentation range が残る。
- `openwrt_ula_prefix` が外部 inventory に無い、RFC4193 ULA `/48` 形式ではない、または public tree / docs に実 prefix が残る。
- `openwrt_frr_in_prefix_list` が `openwrt_frr_prefix_lists` に存在しない。
- `openwrt_frr_prefix_lists` が site BGP VIP subnet の `/32` より広い経路を permit する。
- MetalLB 側 `vip-bgp-pool` / `vip-bgp-adv` が存在しない、または syslog VIP が LoadBalancer として割り当たっていない。
- 現在 IP への SSH rollback path が無い、または host key continuity を確認できない。
- router config backup を private storage へ取得していない状態で `network` tag を apply しようとしている。
- out-of-band recovery を要求する変更なのに、物理 access、OpenWrt recovery path、または router serial console のいずれも確認できていない。
- OpenWrt の tagless apply を要求される。
