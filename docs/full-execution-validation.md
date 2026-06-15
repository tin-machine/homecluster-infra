---
status: current
audience: operator
scope: full-execution-validation
last_reviewed: 2026-06-15
---

# 全体実行検証

## 目的

この文書は、網羅的な source validation と live infrastructure 変更を分ける。ここでの source validation は「repository 内の entrypoint が構文上・静的検証上成立するか」を見る作業であり、live infrastructure 変更は「実 site の state や node に影響する作業」を指す。

この repository は公開可能な subset である。offline check が成功しても、それは含まれる Ansible / Terraform entrypoint が parse / validate できることを示すだけである。documentation 値が live site に適していることや、external inventory が意図した host だけを選ぶことは証明しない。

## 現在の結果

2026-06-05 に、含まれる全 Ansible entrypoint と configuration を持つ全 Terraform root に対して、entrypoint level の exhaustive offline gate が成功した。

この確認で、standalone k3s agent join playbook の未検出だった Ansible syntax error を 1 件見つけて修正した。static check は、top-level の site playbook 2 つに加えて standalone playbook も対象にしている。

2026-06-06 に、実 inventory と private input を使った Gate 2 と、failure domain を k3s staging に限定した Gate 3 を実施した。

2026-06-15 に、公開前の Gate 1 を再実行し、staging PXE release 更新後の OpenWrt / PXE / k3s / observability convergence を確認した。

現在の結果:

| 項目 | 結果 |
| --- | --- |
| private inventory render / validate | 成功 |
| 実 inventory での Ansible `--syntax-check` / `--list-hosts` / `--list-tasks` | 成功 |
| Terraform live plan | `common-crds`、`common-addons`、`common-certificates`、`staging` がすべて差分なし |
| k3s staging controlled convergence | control-plane 1 台、agent 3 台が Ready。kubelet version は全 node で一致 |
| k3s workload health | Pod は `Running` または `Succeeded` のみ |
| k3s networking idempotency | agent 対象の `k3s_networking --check` は `changed=0` |
| OpenWrt DHCP health | DHCP service / process は稼働中 |
| final commit bundle staging | public infra commit と private input commit を staging bundle に pin し、4 Terraform root の plan が差分なし |
| 2026-06-15 Gate 1 | regular static check、strict local scan、backend-free Terraform validate、Ansible syntax / task listing が成功 |
| 2026-06-15 PXE release | staging rootfs は `20260601` bundle で boot。auto stage3 marker は検証時点の upstream latest entry と一致 |
| 2026-06-15 official binhost | `dev-util/maturin` は公式 Gentoo binhost から binary merge。rootfs の glibc 更新は要求されなかった |
| 2026-06-15 distcc | PXE ansible-pull vars 反映後、distcc 対象 node の `distccd` は enabled / active / listen 済み |
| 2026-06-15 k3s / observability | SwitchBot off/on 後、control-plane 1 台、agent 3 台が Ready。Prometheus、Tempo、trace smoke が成功 |

OpenWrt tagless apply は、`bootstrap_python`、storage、rootfs、sysupgrade を `never` gate へ外したうえで router inventory host に限定して実施済みである。反映後、LAN address、DHCP Option 42、dnsmasq syntax、NFS/rpcbind、firewall、FRR process が期待値であることを確認した。

今回の live run で見つけた問題と対応:

| 問題 | 対応 |
| --- | --- |
| repository root から standalone playbook を実行すると role path が足りない | top-level `ansible.cfg` で arm64 / OpenWrt role path を定義した |
| k3s local storage role が既存 runtime mount を見て k3s を停止した後に失敗する | mount / filesystem / format 要否を先に判定し、mount 変更が必要な場合だけ k3s を停止するようにした |
| agent-only play では k3s role が control-plane を発見できない | server group を play scope に含め、role 適用は agent に限定した |
| agent が server と異なる k3s release を取る可能性がある | server の k3s version を読み、agent install version を合わせるようにした |
| `None` 由来の文字列で NetworkManager branch が意図せず動く | nullable input を `default(..., true)` で正規化した |
| Wi-Fi power-save disable が interface / driver 条件で非 0 終了し得る | best-effort として扱い、networking 収束を止めないようにした |
| flannel interface が固定値に寄っていた | private inventory で明示されない場合は default route interface を使うようにした |
| Ansible fact injection の deprecation warning が public code 側に残っていた | role / template の fact 参照を `ansible_facts` 経由へ寄せた |
| OpenWrt network value が public default に寄ると実 router の LAN address や IPv6 ULA prefix を意図しない値へ変更し得る | `openwrt_lan_ipaddr`、`openwrt_ula_prefix`、`openwrt_dhcp_ntp_servers` を external inventory 必須入力として扱い、documentation range や public default は live apply 前 assert で拒否する |
| staging control-plane の `ansible-pull@base` が private repo を credential なしで pull し、HTTP 403 で停止した | external inventory の `openwrt_gentoo_ansible_pull_repo_url` は PXE client が消費する正本 repo `homecluster-infra` にし、`openwrt_gentoo_ansible_pull_netrc_entries` で read-only credential を rootfs へ配る。active rootfs の ansible-pull unit / netrc / vars は `pxe_ansible_pull` gate で更新する |
| push 実行時の staging control-plane hostvars に `stage` と `k3s_server` が出ず、extra-vars なしでは server config が作れなかった | `k3s_stg` group vars に `stage` を持たせ、`role_k3s_stg_server` group vars に generated client vars と同じ `k3s_server` mapping を持たせる |
| k3s server が PXE root overlay 上の overlayfs snapshotter で起動しようとした | server / agent とも `snapshotter: native`、`data-dir: /var/lib/rancher/k3s` を明示する。root overlay と k3s runtime state は分ける |

2026-06-07 の OpenWrt LAN address incident は、この最後の課題が実機で発火したものとして扱う。external inventory に `openwrt_lan_ipaddr` が無い状態で `network` path が到達し、public-safe default / documentation address が live router の LAN address になった。復旧調査では host key continuity を確認したうえで現在の management address へ root SSH できること、Ansible は接続先と user を一時 override すれば実行経路を作れること、`sysupgrade -l` に主要 config が含まれることを確認した。一方で router serial console / OOB host は電源状態に依存し未確認だったため、`network` apply 前の rollback / recovery gate を必須にする。

同日の reboot convergence 確認では、SwitchBot 経由で staging Pi を起動し、control-plane 1 台と agent 3 台が `Ready` になること、kube-system Pod が `Running` または `Succeeded` に収束すること、node pinning した smoke workload が全 node で完了することを確認した。調査中に一時追加した documentation range の LAN alias は削除済みであり、SwitchBot Plug Mini の lease は site LAN 側へ戻った。

同じ確認で、Terraform 管理の observability layer は復旧していないことも確認した。`observability-stg`、`object-storage-stg`、`registry-stg` namespace は存在せず、Helm release metadata も存在しなかった。これは Terraform root の不在ではなく、`homecluster-infra` 側の arm64 playbook に、旧 private repository 側の Terraform auto-apply role / `k3s-ready-terraform.service` 相当がまだ含まれていなかったためである。

その後、`k3s_observability_apply` role を追加し、staging control-plane が `k3s-ready-terraform.service` で state mount 上の revision-pinned infra bundle と site input bundleを検証して、`common-crds`、`common-addons`、`common-certificates`、`staging` の順に Terraform apply する形へ戻した。初回 live run では observability workload、object storage、registry、PVC、LoadBalancer service が復旧した。root overlay 上に k3s runtime があるため apply 中に一時的な DiskPressure が出たが、Terraform 作業コピーを state mount 側へ移し、成功後に削除するようにして root overlay への圧迫を減らした。

また、staging control-plane の `/var/lib/rancher/k3s` は専用 block device ではなく PXE root overlay 側に載っていた。外部 inventory には USB SSD 候補 path が残っているが、実機ではその by-id device が見えておらず、`k3s_local_storage_enabled` も server では有効化されていない。この状態では power cycle で k3s datastore が再作成されるため、Terraform apply 済み workload は自動復旧 service が無い限り消えたままになる。

2026-06-07 の SwitchBot off/on 再検証では、`ansible-pull@base.service`、`ansible-pull@k3s_stg_server.service`、`ansible-pull@terraform_stg.service`、`k3s-ready-terraform.service` がすべて success で終了した。k3s は control-plane 1 台と agent 3 台の 4 node が `Ready` になり、non-Running / non-Succeeded Pod は 0 件だった。Grafana は LoadBalancer 経由で login と `/api/user` が成功し、datasource は Loki、Mimir、Prometheus、Tempo を認識した。Prometheus / Mimir の `up` query、Loki labels、Tempo `/ready`、trace smoke job、node-exporter 4/4、OTel agent 3/3 も成功した。この確認をもって、現在の goal である tagless Ansible convergence から k3s observability login smoke までの staging gate は成功扱いにする。

同じ reboot で、複数の staging agent は stale な k3s join token / node identity を保持しており、server 側に対応する node-password Secret が無いため agent registration が拒否された。復旧は、server 側の token publisher を再実行し、agent 側の node identity と stale token だけを退避して `ansible-pull@k3s_stg_agent.service` を再実行することで完了した。containerd cache は削除していない。

その後、`k3s_local_storage` role に node password sync script と systemd drop-in を追加した。2026-06-15 の SwitchBot off/on 検証では、手動の agent identity 退避なしに staging cluster が 4 node Ready まで収束した。identity reset は agent cert、node password、kubeconfig、load-balancer state に限定し、containerd image cache cleanup とは分ける。

Terraform package の source build 時間についても同日に確認した。`k3s_observability_apply` は `PORTAGE_TMPDIR` を OpenWrt NFS の Terraform state mount 側へ逃がしていたが、Portage の `PKGDIR` である `/srv/gentoo/binpkgs/...` は PXE client 側で NFS mount されておらず、root overlay 上の directory だった。このため `FEATURES=buildpkg` でも build 成果物は reboot で消え、次回 boot の Terraform package 再利用には効かない。対応として、PXE client の `common/change_make_conf` と `k3s_observability_apply` に OpenWrt NFS binpkg cache mount gate を追加し、Pi4/Pi5 の CPU-specific binary が混ざらないよう `PKGDIR` へ CPU key を含める。

残課題:

- Active PXE rootfs の `ansible-pull@.service`、credential、ansible-pull group/client vars は `pxe_ansible_pull` gate で更新済みであり、reboot 後に各 Pi が private `homecluster-infra` から self-converge できることを確認済みである。今後の rootfs refresh でもこの gate を独立して維持する。
- staging control-plane の k3s data-dir を永続化する。旧 private repository の legacy k3s iSCSI storage role を戻すか、USB SSD を接続したうえで server play に `k3s_local_storage` を入れる。どちらの場合も `RequiresMountsFor=/var/lib/rancher/k3s` を server unit に持たせ、mount 不成立なら k3s を起動しない。
- k3s agent の node identity 自動復旧は 2026-06-15 の off/on で手動退避なしの収束を確認済み。今後は server CA / token 世代差が発生した reboot で、node password sync のログと reset 判定を runbook に記録し続ける。
- PXE client 側の Portage binpkg cache を OpenWrt NFS mount に戻す作業は、Terraform package build path では実装済みだが、rootfs / client boot 後の広い build cache 再利用は別 gate で確認する。CPU key 付き `PKGDIR` の buildpkg が reboot 後も残ることを確認する。
- OpenWrt の storage / rootfs / TFTP / sysupgrade は、tagless apply ではなく分けて別 gate にする。
- arm64 の base / workstation / future candidate を含む広い apply は、active group と private inventory scope を再確認してから別 gate にする。
- private inventory 側に古い Ansible fact 参照が残る場合は、private input 側で `ansible_facts` 経由または静的値へ寄せる。public repository には private inventory 本文を置かない。
- public 化直前に commit が増えた場合は、その最終 public commit で Gate 1 を再実行し、private operator workflow に同じ commit SHA を staging bundle として再 pin する。

## Gate 1: 網羅的 offline 検証

site-local inventory、state、kubeconfig、secret を使わずに実行する。

```bash
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 \
  bash scripts/ci/static-check.sh
```

既定では tracked file と untracked non-ignored file を対象にする。公開前に ignored cache / generated artifact まで含めて確認する場合は、次も実行する。

```bash
STATIC_CHECK_STRICT_LOCAL=1 bash scripts/ci/static-check.sh
```

この gate の対象:

- redaction、hard-exclude、large-file、whitespace scan
- Terraform formatting
- backend-free の `terraform init` と `terraform validate`
- Python filter plugin syntax
- 全 OpenWrt / arm64 Ansible entrypoint の syntax check と static task listing

`terraform/env/production` は scope README だけを持つため、意図的に Terraform root ではない。

期待結果: script が `static check ok` で終了する。

この gate は宣言済み entrypoint と Terraform root に対して網羅的である。ただし、すべての runtime path を網羅するわけではない。Ansible `--list-tasks` は task を実行せず、dynamic include や condition をすべて展開するとは限らない。runtime でしか通らない branch は Gate 3 の対象に残る。

## Gate 2: site-local 受け入れ確認

この gate は private operator workflow から実施する。出力を public issue、CI log、artifact にコピーしない。

1. private 側の正本から external inventory を render / validate する。
2. 対象 entrypoint ごとに、実 inventory を使って `--syntax-check`、`--list-hosts`、`--list-tasks` を実行する。
3. host group と `--limit` が、意図した live node だけに解決されることを確認する。
4. documentation address、example selector、example hostname、公開用 runtime value をすべて review 済みの外部入力で置換または override する。OpenWrt では `ansible_host` とは別に `openwrt_lan_ipaddr`、`openwrt_ula_prefix`、`openwrt_dhcp_ntp_servers`、`openwrt_gentoo_server_host`、有効化する `openwrt_enable_*` を review する。
5. 各 Terraform root を actual state と private input で実行し、保存済みかつ review 済みの `terraform plan` で停止する。
6. review 対象の exact commit SHA が target stage で消費される revision であることを確認する。

full Ansible `--check` run を read-only として扱わない。一部の command task や role は意図的に `check_mode: false` を使う。

## 現在の live apply blocker

scope を絞らない live apply の前に、次を解決する。

| Blocker | 重要な理由 |
| --- | --- |
| 外部入力が render されていない | site-specific Terraform variable と staging Helm site values は live fallback を持たない。plan 前に private input を render / validate する必要がある。 |
| External inventory scope の review が必要 | broad group には、現在意図した cluster member だけでなく rebuild、migration、future candidate が含まれる場合がある。 |
| Stage revision が `main` と異なる可能性がある | PXE client は stage branch を消費する。local `main` を test しても、reboot 後に同じ revision が動くことは証明しない。 |
| OpenWrt LAN/PXE 入力が未 review | `openwrt_lan_ipaddr`、`openwrt_ula_prefix`、DHCP Option 42、PXE serveraddress、FRR/syslog の実値が external inventory にあり、documentation range / public default fallback が残っていないことを確認する必要がある。 |
| OpenWrt site play の blast radius が大きい | tag なし実行は storage partitioning/formatting、rootfs rebuild、network、firewall、DHCP、NFS、TFTP、sysupgrade path に到達し得る。 |
| OpenWrt rollback / OOB recovery が未確認 | `network` tag は management address を変えるため、現在 IP への SSH rollback path、host key continuity、private config backup、必要なら serial / physical recovery path を確認する必要がある。 |

## Gate 3: controlled staging 収束確認

Gate 2 が通った後、すべての entrypoint を一度に実行せず、failure domain ごとに apply する。

1. 承認済み commit SHA と対応する private input revision を 1 つの bundle として pin する。router config、PXE release、Terraform state の rollback point を記録する。
2. review 済み tag ごとに OpenWrt role を apply する。storage formatting、rootfs replacement、TFTP switching、sysupgrade は別承認に分ける。
3. 意図した staging member に一致する明示 `--limit` で arm64 role を apply する。standalone k3s join playbook は、server group と agent group の review 後にだけ実行する。
4. Terraform root を依存順に分けて review / apply する。順序は `common-crds`、`common-addons`、`common-certificates`、`staging`。
5. reboot convergence、k3s node readiness、workload health、storage、networking、observability を確認する。
6. 同じ scoped apply を再実行し、idempotency を確認する。

post-apply service check には [k3s observability validation checklist](k3s-observability-validation-checklist.md) を使う。

OpenWrt の tag 分割 plan は [OpenWrt live apply plan](openwrt-live-apply-plan.md) を使う。この plan は `network`、`dhcp`、`openwrt_frr`、`openwrt_syslog_remote`、`firewall`、`openwrt_banip` までを対象にし、storage、rootfs、TFTP artifact、sysupgrade は別 gate として扱う。

## 判断ルール

- Gate 1 failure: source または validation tooling を修正する。
- Gate 2 failure: private input、target selection、revision promotion を修正する。
- Gate 3 failure: 現在の failure domain で停止し、その rollback plan を使う。
- 3 つの gate が同じ承認済み commit SHA に対して成功した場合だけ、repository が fully converged したと呼ぶ。

含まれる scope は、実行可能な公開側の正本として維持する。private workflow は input、state、approval、validation result を保持するが、別実装 fork は維持しない。
