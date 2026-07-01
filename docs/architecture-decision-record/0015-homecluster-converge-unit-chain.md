---
status: proposed
audience: human-ai
scope: homecluster-converge-unit-chain
last_reviewed: 2026-07-01
---

# ADR 0015: homecluster コンバージ・ユニット・チェーンの段階的移行

## 状況

現行の live chain は、`ansible-pull@base.service` から `OnSuccess=` を介して
`k3s_stg_storage` へ進み、続いて `k3s_stg_server` / `k3s_stg_agent`、最後に
`terraform_stg` / `k3s-ready-terraform.service` style の適用へ進む構成である。

この構成は cold boot 時に storage を k3s より先へ進める実運用上の問題を解いている。一方で unit 名は
Ansible role 名と staging 名に寄っており、将来の運用・検証では「base」「storage」「k3s converge」
「terraform apply」という domain boundary が見えにくい。これを domain 単位の service 名へ整理するため、
段階的な移行パスを定義する。

## 決定

以下の domain name に基づいた unit chain への移行を目指す。

```text
homecluster-base.service
  -> homecluster-storage.service
  -> homecluster-k3s-converge.service
  -> homecluster-terraform.service
```

この ADR は source / design contract であり、現時点の live unit 名を変更しない。`homecluster-*`
名は将来の domain-name layer であり、既存の `ansible-pull@*` / `k3s_stg_*` chain の上へ
alias / wrapper として段階的に重ねる。

各ユニットの責務は以下の通りとする：

- `homecluster-base.service`: base convergence のみを実行する。storage login、k3s start、
  Terraform apply を直接持たない。
- `homecluster-storage.service`: iSCSI login または local block storage discovery を行い、
  `/var/lib/rancher/k3s` が mount 済みで、PXE root overlay ではないことを明示的に証明する。
- `homecluster-k3s-converge.service`: broad install / config の所有権を持たない。storage、
  config、token / CA hash などの前提確認後にだけ `k3s.service` を start し、server では
  readyz、agent では node readiness を確認する。
- `k3s.service`: k3s daemon そのものを保持する daemon-only unit とする。direct boot enable は行わず、
  `RequiresMountsFor=/var/lib/rancher/k3s` と
  `ConditionPathIsMountPoint=/var/lib/rancher/k3s` を defense-in-depth として維持する。
- `homecluster-terraform.service`: Kubernetes API、CRD、addon readiness、site input、state mount、
  approved revision を確認してから、revision-pinned Terraform / Helm apply を実行する。

## 段階

1. 現行の `OnSuccess=` chain を維持し、source validator と runbook verifier で
  mount-before-k3s と k3s direct boot disabled を守る。
2. `homecluster-*` 名の責務を ADR と source validator で固定する。この段階では live unit 名を変えない。
3. alias / wrapper unit を source-only で追加し、既存 `ansible-pull@*` role chain との対応を明示する。
4. post-merge apply で rootfs / unit / drop-in を更新し、boot-order gate と integrated iSCSI verifier で
  current chain と alias layer の両方を確認する。
5. SwitchBot off/on で cold boot convergence を確認し、問題がなければ live start point を
  `homecluster-*` 名へ寄せる。

## 理由

### なぜ `OnSuccess=` スタイルのキューイングが `Requires=` 依存ジョブより好ましいのか

boot 時の convergence 問題では、`Requires=` だけで stage をつなぐと、base または storage が同じ
boot transaction 内で一時的に失敗したとき、後続 job は dependency failure として失敗する。
依存先が後で成功しても、失敗済みの dependent job は自動では queue に戻らない。

`OnSuccess=` style の queueing は、「前段が成功した瞬間に次段を queue する」という責務を unit
boundary に残せる。各 stage の journal と retry point が読みやすく、operator や future controller が
必要な stage だけを再実行しやすい。

### なぜ `k3s.service` は daemon-only であり、直接の boot enable を行わないのか

`k3s.service` は k3s daemon そのものを保持する既存 unit である。これを direct boot enable すると、
configuration management と storage convergence より先に k3s が動き、PXE root overlay 側へ
node identity、kubelet state、containerd state を再生成し得る。

`RequiresMountsFor=` と `ConditionPathIsMountPoint=` は最後の防御線として維持するが、これだけを
readiness contract にはしない。start の判断は storage と config の前提を知っている
`homecluster-k3s-converge.service` 側へ置く。

## 不採用案

### `Requires=` / `After=` だけで full chain を表現する

不採用。

ordering は表現できるが、boot transaction 内の一時失敗後に dependent job を再 queue する責務が残る。
この責務を unit dependency だけへ隠すと、復旧 point が読みにくい。

### base role の末尾で storage / k3s / terraform を直接実行する

不採用。

base が巨大な router になり、journal、retry、failure domain が混ざる。domain 名 unit へ分ける目的にも
反する。

### `k3s.service` を再び direct boot enable する

不採用。

storage mount 前の k3s 起動 race を再導入し、PXE root overlay への host-specific state 再生成を招く。

## 影響

本設計への移行に伴い、将来の live migration では以下の acceptance set を満たすことを確認する。

- static checks が pass する。
- post-merge apply で active rootfs / unit / drop-in / staged bundle が approved revision に揃う。
- boot-order gate で `/var/lib/rancher/k3s` が k3s start 前に mount 済みであることを確認する。
- integrated iSCSI verifier が OpenWrt target、initiator session、mount、k3s recovery、containerd residue を確認する。
- SwitchBot off/on で cold boot convergence を確認する。

## 見直し条件

- ユニット間の依存関係が複雑になり、`OnSuccess=` チェーンでは制御不能になった場合。
- `k3s.service` を直接 boot enable する方が、現在のマウント制約下で安全であることが証明された場合。
