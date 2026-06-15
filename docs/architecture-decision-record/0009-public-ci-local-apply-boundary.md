---
status: accepted
audience: human-ai
scope: public-ci-local-apply-boundary
last_reviewed: 2026-05-31
---

# ADR 0009: 公開 CI と local apply を直接接続しない

## 状況

公開 repository では、pull request、workflow input、branch 名、commit message、issue / comment などが untrusted input になる。

public GitHub Actions から家庭内 LAN、k3s cluster、router、private inventory、secret へ直接到達できる構成にすると、公開 contribution surface と local apply surface がつながる。

CI status、artifact、job summary、runner label からも内部構成が推測される可能性がある。

## 決定

public repository の CI は lint / [static check](../../scripts/ci/static-check.sh) / unit test までにする。

public repository に self-hosted runner を登録して、家庭内 LAN や local cluster へ到達させない。

local apply は private 側の controller / runbook で扱う。対象 revision は branch 名ではなく、検証済み commit SHA で固定する。

公開 CI へ返す status は、必要最小限の結果に留める。詳細 log、実 host、internal URL、secret name、runner name は出さない。

## 理由

public PR 由来の文字列を shell、Ansible extra vars、Terraform variables、AI prompt、Slack 通知、local apply へ未加工で渡す経路を避ける。

self-hosted runner を public repository に登録しないことで、public workflow から local network へ到達する経路を作らない。

local apply を private 側に閉じることで、inventory、kubeconfig、tfvars、SSH key、controller token を public repository に置かずに済む。

## 不採用案

### public GitHub Actions から直接 apply する

不採用。

public workflow が secret と local network に近づきすぎる。workflow input や PR content を完全に無害化する負荷も高い。

### public repository に self-hosted runner を置く

不採用。

runner label、queue、failure log、artifact から内部実行基盤の存在や構成が漏れやすい。public PR から runner を使う攻撃面にもなる。

### branch 名で local apply 対象を決める

不採用。

branch 名は mutable であり、文字列としても untrusted input である。apply 対象は検証済み commit SHA に固定する。

## 影響

公開 CI だけでは実機 apply の成功を証明しない。

local apply の承認、取得、検証、実行、報告を private 側で設計する必要がある。

public contributor 向けには、実機 apply ではなく static validation の範囲を明確にする。

## 見直し条件

- public repository と local environment の間に、十分に隔離された read-only validation environment を用意できた場合。
- GitHub Actions 以外の attest / promotion workflow が整い、untrusted input を local apply へ渡さない保証を強められた場合。

## 完了メモ (2026-06-14)

この repository の [GitHub Actions workflow](../../.github/workflows/static-check.yml) は `ubuntu-latest` 上の static check のみに限定し、`contents: read`、checkout credential persistence disabled、commit SHA pin した checkout action を使う構成にしている。public CI から local apply、inventory、kubeconfig、Terraform state、家庭内 network へ到達する経路は作っていない。

private controller 側では、staging bundle admission の初期実装を完了した。[cluster-side apply wrapper](../../ansible/arm64/roles/k3s_observability_apply/templates/k3s-observability-apply.sh.j2) は full commit SHA と private input revision を bundle ID に束ねた admission gate の結果を消費する。same-repository PR preview は PR number から exact head SHA へ解決し、fork / cross-repository PR、unexpected base ref、allowlist 外 author を拒否する。

この完了メモで完了扱いにするのは、public CI と local apply を直接接続しない境界、および staging bundle admission の初期実装である。upstream freshness、force push / ref 移動拒否、plan summary、manual approval、post-apply validation、rollback metadata は private controller 側の後続課題として残す。
