---
status: proposed
audience: human-ai
scope: k3s-converge-wrapper-contract
last_reviewed: 2026-07-01
---

# ADR 0014: k3s daemon start を converge wrapper へ段階的に寄せる

## 状況

PXE Gentoo staging node は root overlay を disposable にし、k3s runtime state は local block backed
filesystem へ置く。iSCSI-backed node では、`/var/lib/rancher/k3s` mount 前に `k3s.service` が起動すると、
root overlay 側へ node identity、kubelet state、containerd state を再生成し得る。

現行構成では、`ansible-pull@base.service` の成功後に `OnSuccess=` で `k3s_stg_storage` を queue し、
その後 `k3s_stg_server` / `k3s_stg_agent` を queue する。active rootfs では k3s daemon の direct boot
enable symlink を消し、`RequiresMountsFor=/var/lib/rancher/k3s` と
`ConditionPathIsMountPoint=/var/lib/rancher/k3s` を維持している。

この構成は cold boot の mount-before-k3s 問題を解消するが、`k3s_stg_server` / `k3s_stg_agent` play 内の
`xanmanning.k3s` は install/configure と daemon start を同じ role surface に持つ。将来の
`homecluster-k3s-converge.service` では、daemon start と readiness 判定をより明示的な wrapper へ寄せたい。

## 決定

`k3s_converge` wrapper contract を導入する。ただし初期段階では live start を担わせず、`--check-only`
の source-only/read-only probe から始める。

最終的な責務境界は次の通りにする。

- `k3s_stg_storage`
  - iSCSI login、block device appearance、mountpoint、mount options を収束させる。
- `k3s_stg_server` / `k3s_stg_agent`
  - k3s binary、config、systemd unit/drop-in、registry、networking config を収束させる。
  - daemon start/readiness の最終判断は段階的に wrapper へ移す。
- `k3s_converge`
  - mountpoint、service name、server endpoint、token / CA hash の存在確認、node identity 前提を確認する。
  - start を担う段階では、前提確認後に `systemctl start k3s.service` を実行し、readyz / node readiness を確認する。
  - token 値、kubeconfig、site-specific endpoint の meaningful default は持たない。
- `k3s.service`
  - k3s daemon そのものを保持する既存 unit。
  - direct boot enable しない。
  - `RequiresMountsFor=/var/lib/rancher/k3s` と `ConditionPathIsMountPoint=/var/lib/rancher/k3s` を維持する。

## 段階

1. `k3s_converge --check-only` contract を追加する。`systemctl start`、mount、iSCSI login、
  Secret 更新、node repair は行わず、example inventory の syntax / list-tasks と static check で検証する。
2. agent side から wrapper 配置を検討する。agent play は既に service restart defer を使っているため、
  server より start 分離しやすい。
3. server side の start 抑制可否を source-only で確認する。upstream role の start semantics を読んでから
  方針を決め、role 後に k3s を stop する形で帳尻を合わせない。
4. agent side の start 分離では、`xanmanning.k3s` を `k3s_state: downloaded` に寄せる。
  `downloaded` は k3s binary download までで、`/usr/local/bin/k3s` symlink、config、unit、
  token file は配置しない。agent config は既存 `k3s_networking` role が最終内容を配置し、
  `k3s_defer_service_restart` で restart を抑制できるため、local role は当面 binary link、unit、
  token file 配置を担う。
  この local role は `Restart k3s systemd` handler や `systemctl start/restart` を直接持たない。
  2026-07-01 時点では、local role の最初の scaffold として `k3s_agent_install_config` を追加した。
  この scaffold は未接続で、public-safe default、assert task、binary link task だけを持つ。daemon
  lifecycle はまだ実装しない。
5. `k3s_converge` が live start / restart を担う段階では、agent play に upstream
  `Restart k3s systemd` handler 由来の start point が残っていないことを source validator で確認する。
6. wrapper が live start を担う段階で、post-merge apply と SwitchBot off/on を 1 セットで検証する。
7. 十分に安定した後で `homecluster-k3s-converge.service` への unit 名分離を検討する。

## 理由

`OnSuccess=` chain は現時点で live staging の cold boot を成立させている。ここへいきなり
`homecluster-k3s-converge.service` を入れると、既存 role 由来の start と wrapper 由来の start が二重化し、
失敗時の journal と rollback point が読みにくくなる。

`--check-only` から始めると、wrapper の入力境界、出力、failure reason、static validation を先に固められる。
この段階では live apply しても daemon start 責務は変わらないため、rollback が容易である。

server node は token publish と API readyz の起点である。agent より先に server start を分離すると
blast radius が大きいため、agent side の read-only / check-only から広げる。

2026-07-01 の source read-only 確認では、agent 側でも `xanmanning.k3s` の
`ensure_installed_node.yml` が binary link、config、unit、token file 更新時に `Restart k3s systemd`
handler を notify し得ることが分かった。このため、`k3s_converge` を agent play の後段に置くだけでは
daemon start の責務は移らない。live start を wrapper へ寄せる前に、upstream role を `k3s_state: downloaded`
へ落とし、local install/config/unit/token 配置 role を挟む必要がある。

## 不採用案

### すぐに `homecluster-k3s-converge.service` を boot chain に入れる

不採用。

現行 role の start semantics を分離する前に unit を増やすと、start point が二重化する。

### `xanmanning.k3s` 実行後に k3s を stop して wrapper で start し直す

不採用。

storage / identity / token 問題の切り分けが難しくなり、containerd residue や node password mismatch を
不要に誘発し得る。

### base role の末尾で storage / k3s / terraform を直接呼ぶ

不採用。

base が巨大な role router になり、journal、retry、failure domain が読みにくくなる。

## 影響

初期実装は check-only なので live behavior は変えない。behavior を変える段階では、private operator workflow で
post-merge apply、active rootfs / DHCP Option 224 verifier、boot-order gate、integrated iSCSI verifier、
SwitchBot off/on を 1 つの acceptance set として扱う。

public repository には real host、private address、token、kubeconfig、raw operation log を置かない。
site-specific endpoint や token source は external inventory または private runtime input から渡す。

## 見直し条件

- upstream k3s role が server start 抑制に適した option を持たない場合。
- check-only wrapper が live 前提確認として不十分で、domain-specific readiness を role 内に戻す方が単純だと分かった場合。
- `OnSuccess=` chain より単一 state machine unit の方が明確になるほど分岐が増えた場合。
