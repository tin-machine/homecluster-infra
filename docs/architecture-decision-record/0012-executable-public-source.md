---
status: accepted
audience: human-ai
scope: executable-public-source
last_reviewed: 2026-06-05
---

# ADR 0012: 公開 repository を実行可能な正本にする

## 状況

公開用に skeleton や sample だけを維持すると、実運用コードとの同期が必要になり、長期的には stale になりやすい。

一方で、実 inventory、site 固有値、secret、state、kubeconfig、operation log を公開 repository に含めることはできない。

## 決定

この repository の対象範囲は、private input を注入すれば実 site へ plan / apply できる [共通実装](../../ansible/arm64/site.yml) として維持する。

対象範囲に private implementation fork を作らない。private 側が持つのは、実 inventory、site 固有値、secret、state、kubeconfig、local apply controller、runbook、validation result に限定する。

[Ansible](../../ansible/openwrt/site.yml) は外部 `../inventory.yml` を使う。[Terraform](../../terraform/env/staging/variables.tf) は root-specific `tfvars.json`、[Helm](../../terraform/modules/observability_stack/variables.tf) は chart-specific site values を外部から受け取る。Terraform が Ansible inventory を解析する構成にはしない。

site 固有入力が無い場合や、documentation / example 値が実入力に残る場合は fail closed にする。

公開 CI は [static validation](../../scripts/ci/static-check.sh) までに留める。実 site では、private controller / operator workflow が検証済み public commit SHA を pin し、plan、controlled apply、post-apply validation を継続的に行う。

private controller は、commit 済み public infra revision と commit 済み private input revision を同じ bundle ID に束ねる。[cluster-side apply](../../ansible/arm64/roles/k3s_observability_apply/templates/k3s-observability-apply.sh.j2) は revision marker が一致しない bundle を適用しない。

## 理由

公開コードと実運用コードを同じにすることで、公開後も実環境で成立するかを検証し続けられる。

入力形式を Ansible / Terraform / Helm の責務ごとに分けることで、単一 inventory を複雑な変換ロジックで共有する必要がない。

公開 CI と local apply を分離したまま、公開 commit が実 site で使われていることを private 側で確認できる。

## 不採用案

### 公開 sample と private 実装を別々に持つ

不採用。

差分同期が必要になり、公開 sample が実際には動かない状態を検出しにくい。

### Terraform が Ansible inventory を直接解析する

不採用。

Terraform root ごとに必要な値が異なり、Ansible group / host variable の構造変更が Terraform 実行へ波及する。変換処理も複雑になる。

### public GitHub Actions から実 site へ apply する

不採用。

public contribution surface と家庭内 LAN / secret / state を接続するため、ADR 0009 の境界に反する。

## 影響

- site 固有変数には public fallback default を持たせない。
- staging Helm release は、外部の site values file が全 chart 分存在することを要求する。
- public static check は実入力なしで `terraform validate` まで行う。live plan / apply は private 側で行う。
- live apply は exact public revision と matching private input revision を持つ revision-pinned bundle から行う。
- 実 site で public commit SHA を定期的に plan / apply し、再起動後の収束と idempotency を確認する必要がある。

## 見直し条件

- 対象範囲の実装を公開可能な状態に保てず、private fork が不可避になった場合。
- site input の root / chart 分割が運用負荷に対して過剰になった場合。
- public CI と local environment の間に、十分に隔離された安全な validation boundary を用意できた場合。

## 完了メモ (2026-06-14)

この repository は、private input を注入すれば実 site へ plan / apply できる共通実装として維持する形に整理済みである。[README](../../README.md)、[外部入力](../site-input-contract.md)、[example inventory](../../examples/inventory.yml)、[static check](../../scripts/ci/static-check.sh)、[Terraform root 分割](../../terraform/env/staging/main.tf)、[Helm site values contract](../../terraform/modules/observability_stack/variables.tf)、public-safe fail-closed default はこの方針に合わせて整備した。

private workflow 側では、exact public infra revision と private input revision を bundle ID に束ねる staging bundle admission を実装済みである。bundle には infra revision、inventory revision、[apply contract marker](../../ansible/arm64/roles/k3s_observability_apply/files/apply-contract-version)、site input marker を残し、[cluster-side apply wrapper](../../ansible/arm64/roles/k3s_observability_apply/templates/k3s-observability-apply.sh.j2) が revision marker の不一致を検出できる構成にした。

この完了メモで完了扱いにするのは、公開側 source を executable source of truth として保つための repository 構成、外部入力、static validation、staging bundle admission の初期実装である。plan artifact、manual approval、post-apply validation、retention / rollback は private workflow 側の後続課題として残す。
