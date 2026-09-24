---
status: accepted
audience: human-ai
scope: cloudflare-dns-terraform-root
last_reviewed: 2026-09-25
---

# ADR 0016: Cloudflare DNS を独立 Terraform root として既存 infra repository で管理する

## 状況

homecluster の public infrastructure source は、OpenWrt、PXE Gentoo、k3s と Terraform add-on を
`homecluster-infra` に集約し、site 固有値、secret、state を private boundary から注入している。

Cloudflare DNS も Terraform で管理したい。一方、DNS は外部 control plane であり、誤変更すると Web だけで
なく mail routing や domain verification にも影響し得る。また、実 hostname の一覧を public source に
置くと、通常の DNS lookup より容易に site topology を列挙できる。

repository を Cloudflare 専用に分ければ権限境界は分かりやすいが、homecluster の小規模運用では repository
数と cross-repository coordination が増える。

## 決定

Cloudflare DNS の reusable Terraform implementation は `homecluster-infra` に置く。ただし
`terraform/env/cloudflare-dns` を独立 root とし、k3s / Kubernetes root と state を共有しない。

境界は次とする。

- public source は provider、resource schema、validation、公開可能な運用契約だけを持つ。
- 実 `zone_id`、record name、record content、TTL、proxy policy は private root-specific site input に置く。
- Cloudflare API token は Terraform variable や tfvars に入れず、runtime の
  `CLOUDFLARE_API_TOKEN` から provider へ渡す。
- token は管理対象 zone に限定し、DNS 変更に必要な最小権限だけを与える。
- DNS state と plan は private artifact とし、cluster Terraform state と分離する。
- public CI は format、redaction、source-only validation に限定し、Cloudflare credential を持たず
  `plan` / `apply` を実行しない。
- existing DNS record は最初の managed apply より前に import する。
- live apply controller への統合は、この source/input contract が成立した後の別変更とする。
- 初期 backend は既存の operator/controller-managed state 境界を使い、R2 backend は採用しない。

初期 record surface は `A`、`AAAA`、`CNAME`、`MX`、`TXT` に限定する。structured data が必要な
record type は、provider schema に合わせて明示的に拡張する。

## 理由

repository 境界より Terraform root / state / credential 境界の方が、実際の apply blast radius に直接効く。
同じ repository に置いても state を共有しなければ、DNS apply が k3s provider refresh や cluster state lock
を要求しない。

既存の public/private input contract を再利用すると、新 repository、別 CI policy、別 dependency update
policy を増やさずに済む。一方、実 zone / hostname を private input に残すことで、public repository が
site-specific DNS inventory になることを避けられる。

API token を runtime environment に限定すると、provider credential と declarative DNS data を分離できる。
zone-scoped token にすることで、credential leak 時の account-wide blast radius も抑えられる。

R2 は将来の remote backend 候補にはなるが、最初の DNS migration と同時に state backend まで
Cloudflare に寄せると、変更点と failure domain が増える。まず既存 state boundary で DNS 管理を成立させる。

## 不採用案

### Cloudflare 専用 repository を新設する

現時点では不採用。

独立 team、独立 release cadence、独立 access control が必要な規模ではない。Terraform root と state を
分ければ必要な blast-radius separation は得られる。

### k3s staging root に Cloudflare resource を追加する

不採用。

DNS と cluster の lifecycle、credential、state lock、failure mode が結合する。

### API token を SOPS tfvars に保存する

不採用。

SOPS は site input の保管には使えるが、provider credential を declarative input と同じ経路に載せる必要は
ない。token は runtime credential として別境界に置く。

### public GitHub Actions から apply する

不採用。

public repository に外部 production control-plane credential を持たせず、既存の public CI / private apply
boundary を維持する。

### 初期 state backend を Cloudflare R2 にする

現時点では不採用。

DNS migration、credential、remote backend migration を同時に導入せず、まず独立 DNS state の運用を
成立させる。

## 影響

- `homecluster-infra` に external DNS という新しい infrastructure domain が増える。
- private input workflow は optional な `cloudflare-dns.tfvars.json` を生成・検証できる必要がある。
- Cloudflare provider version と schema は cluster provider とは独立して更新する。
- DNS record の削除は即時の外部影響があるため、将来の controller integration では plan review と
  operator approval を明示 gate にする。
- Email Routing、Zero Trust、Workers、R2 などを追加する場合も、同じ repository に機械的に詰め込まず、
  lifecycle と state blast radius に応じて root を分ける。

## 見直し条件

- Cloudflare 管理対象が DNS を越えて増え、独立 repository / access policy の方が変更境界を明確にできる場合。
- 複数 operator / team が Cloudflare と homecluster cluster infrastructure を別々に管理するようになった場合。
- remote state の可用性、versioning、credential separation を評価し、R2 backend の方が既存 backend より
  運用上明確に優れる場合。
- Cloudflare DNS apply を private controller の fixed operation として安定運用できる契約が固まった場合。
