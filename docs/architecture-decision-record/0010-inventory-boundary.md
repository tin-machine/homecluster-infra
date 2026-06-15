---
status: accepted
audience: human-ai
scope: inventory-boundary
last_reviewed: 2026-05-31
---

# ADR 0010: 実 inventory は repository 外に置く

## 状況

Ansible inventory には、host 名、address、device path、site-specific vars、secret 参照、credential、token、SSID / PSK などが入り得る。

公開 repository では、実 inventory を含めると家庭内 topology や credential boundary が漏れる。

一方で、Ansible role / playbook は inventory path を前提に動く。multi-repository 化しても同じ実行感を保つには、path contract を固定する必要がある。

## 決定

実 inventory は公開 repository に置かない。

各 repository から見た inventory path は `../inventory.yml` を標準 contract とする。公開側には [examples/inventory.yml](../../examples/inventory.yml) または README 内の dummy inventory だけを置く。

実 inventory と secret は private 側で管理し、raw secret は原則として暗号化する。

公開側の CI は実 `../inventory.yml` を前提にしない。

## 理由

repository root の外へ inventory を置くことで、公開 tree に実 host / secret が混入する可能性を下げられる。

`../inventory.yml` という path contract を保てば、複数 repository に分けても playbook 実行時の指定を大きく変えずに済む。

公開側は [example inventory](../../examples/inventory.yml) だけを持つため、external contributor や [CI](../../.github/workflows/static-check.yml) は dummy values で static validation できる。

## 不採用案

### 実 inventory を公開 repository に含める

不採用。

実 host、address、secret 参照、site-specific path が公開される。

### repository ごとに inventory path を変える

不採用。

multi-repository 化後に実行手順が分散し、operation error が増える。

### private repository なら raw secret をそのまま置く

不採用。

private repository でも、account、deploy key、controller、client host のいずれかが侵害されればまとめて読まれる。raw secret は暗号化または外部注入を基本にする。

## 影響

[playbook](../../ansible/openwrt/site.yml) は実 inventory が無い環境でも、syntax check や static validation ができるように example path を用意する必要がある。

[role defaults](../../ansible/openwrt/roles/openwrt_network/defaults/main.yml) や docs に実値を fallback として置かない。

site-specific / live-impacting value には meaningful public default を置かない。IP、subnet、route、storage device、BGP policy、syslog destination、PXE / NFS / TFTP endpoint は external inventory を正とし、欠けている場合は [fail closed](../../ansible/openwrt/roles/openwrt_network/tasks/main.yml) にする。`enabled: false`、empty list、empty string + assert のような no-op / fail-closed default は許可する。

2026-06-07 の OpenWrt LAN address incident では、external inventory で欠けた desired LAN address が public-safe documentation default に寄り、live router の management address が変わった。これは「公開用 default は安全に見えるが、live apply では危険な desired state になり得る」例として扱う。

inventory key の構造変更時は、secret 値を出さずに key / membership だけを検査する helper を使う。

## 見直し条件

- inventory の構造が repository ごとに大きく分かれ、単一 `../inventory.yml` contract がかえって複雑になった場合。
- secret management を外部 secret manager に完全移行し、inventory が secret 参照だけを持つ薄い構造になった場合。
