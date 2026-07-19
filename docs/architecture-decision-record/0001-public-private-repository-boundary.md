---
status: accepted
audience: human-ai
scope: public-private-repository-boundary
last_reviewed: 2026-05-31
---

# ADR 0001: 公開 repository と private 実運用 repository を分ける

## 状況

この repository には、OpenWrt / PXE / k3s / Terraform の実装に加えて、家庭内環境の operation 手順、troubleshooting raw log、実 host 固有値、secret 注入境界、desktop / scanner 検証などが混在している。

公開すると、source tree だけでなく git history、CI artifact、search engine cache、fork 先にも情報が残る。後から削除や rotate をしても、公開済み history を完全に回収することは難しい。

一方で、[OpenWrt](../../ansible/openwrt/site.yml) / [PXE](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/main.yml) / [k3s](../../ansible/arm64/site.yml) の設計判断と再現可能な実装は公開する価値がある。

## 決定

現行 repository は private 実運用 repository として残す。

公開用 repository は、sanitize 済み working tree から新規 repository として作り、既存 `.git` history を持ち込まない。

公開側に入れるもの:

- [OpenWrt](../../ansible/openwrt/site.yml) / [PXE](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/main.yml) / [k3s](../../ansible/arm64/site.yml) / [Terraform](../../terraform/env/staging/main.tf) の公開可能な実装。
- 実値を受け取らず外部 inventory から注入する、PicoClaw などの opt-in service role。token、host 固有値、生成済み runtime config は含めない。
- 実値を含まない [example inventory](../../examples/inventory.yml)。
- 公開可能な Architecture Decision Record。
- lint / static check 用の [CI workflow](../../.github/workflows/static-check.yml) と [static check script](../../scripts/ci/static-check.sh)。

private 側へ残すもの:

- operation / troubleshooting 原本。
- 実 host、private IP、MAC、serial、個人 path。
- secret、tfstate、tfvars、kubeconfig。
- scanner / desktop / local apply controller など、公開範囲から外す実運用要素。

## 理由

履歴を持ち越さないことで、過去 commit に含まれる secret / tfstate / 個人情報を public history に出さずに済む。

公開する情報を「現在の sanitize 済み tree」と「意図して書いた ADR」に限定できるため、公開前 review の対象を小さくできる。

private 実運用 repository を残すことで、家庭内検証環境の復旧手順や raw log を削りすぎずに維持できる。

## 不採用案

### 現行 repository をそのまま公開する

不採用。

history、operation docs、troubleshooting docs、tracked state、実 host 固有値をすべて監査・purge・rotate する必要がある。漏れた場合の回収が難しい。

### public skeleton repository だけを作る

不採用。

薄い skeleton だけでは実装と設計判断の対応関係が失われ、private repository との差分同期が必要になる。公開する価値が小さく、長期的に stale になりやすい。

## 影響

公開側と private 側の差分運用が発生する。

private 側で進んだ設計判断を公開側へ戻す場合は、raw context をそのまま移さず、ADR や sanitized implementation として書き直す必要がある。

## 見直し条件

- 公開側と private 側の差分同期が運用不能になった場合。
- public repository に入れる範囲が広がり、private 側の実運用情報を参照しないと CI / test が成立しなくなった場合。
- repository history を安全に公開できるだけの secret scan、history purge、rotate 手順が確立した場合。
