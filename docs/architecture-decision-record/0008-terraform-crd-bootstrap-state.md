---
status: accepted
audience: human-ai
scope: terraform-crd-bootstrap-state
last_reviewed: 2026-05-31
---

# ADR 0008: Terraform CRD bootstrap state を依存 addon state から分ける

## 状況

Terraform の [`kubernetes_manifest`](../../terraform/env/common-addons/main.tf) や [Helm release](../../terraform/modules/observability_stack/main.tf) は、plan / refresh 時点で Kubernetes API の GVK discovery に依存する。

CRD がまだ存在しない fresh cluster で、CRD を提供する chart とその CRD に依存する manifest を同じ state / apply に混ぜると、plan 時点で discovery failure になりやすい。

cert-manager、MetalLB などは CRD と dependent resource の順序が重要である。

## 決定

Terraform state は、CRD bootstrap と依存 addon を分ける。

標準順序は次の通りにする。

1. CRD を提供する [layer](../../terraform/env/common-crds/main.tf)。
2. CRD に依存する [addon layer](../../terraform/env/common-addons/main.tf)。
3. certificate / issuer など、CRD availability を前提にする [layer](../../terraform/env/common-certificates/main.tf)。
4. environment-specific app / observability [layer](../../terraform/env/staging/main.tf)。

workspaces で環境を切り替えるのではなく、環境別 directory / state で分離する。

## 理由

fresh cluster で CRD が存在しない状態でも、まず CRD layer だけを apply できる。

dependent manifest の plan / refresh が、CRD discovery 成功後に実行される。

state が責務ごとに分かれるため、復旧時の apply order を固定しやすい。

## 不採用案

### CRD と dependent manifest を同じ state に入れる

不採用。

Terraform の plan / refresh が CRD availability に引きずられ、fresh cluster bootstrap で失敗しやすい。

### Helm chart の CRD install にすべて任せる

不採用。

chart により CRD lifecycle の扱いが異なり、dependent resource の apply order を Terraform 側で説明しにくい。

### Terraform workspaces で環境を切り替える

不採用。

家庭内検証環境では directory / state を分けた方が、plan 対象と復旧順序を明示しやすい。

## 影響

apply 手順が複数段になる。

CI / runbook / local apply controller は、state directory の順序を理解する必要がある。

state 間依存を過剰に増やさず、apply order と provider discovery のために必要な最小分割に留める。

## 見直し条件

- Terraform provider 側で CRD discovery の失敗を安全に扱えるようになった場合。
- cluster bootstrap と addon 管理を別 tool に分け、Terraform が CRD 初期化を扱わなくなった場合。
