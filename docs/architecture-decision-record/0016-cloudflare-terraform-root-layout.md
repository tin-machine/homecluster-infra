---
status: accepted
audience: human-ai
scope: cloudflare-terraform-root-layout
last_reviewed: 2026-09-25
---

# ADR 0016: Cloudflare shared infrastructure を category 配下の独立 Terraform root で管理する

## 状況

homecluster の public infrastructure source は、OpenWrt、PXE Gentoo、k3s と Terraform add-on を
`homecluster-infra` に集約し、site 固有値、secret、state を private boundary から注入している。

Cloudflare DNS の Terraform 管理を開始したが、Cloudflare には DNS 以外にも Email Routing、Zero Trust、
Tunnel / Access、WAF、cache、R2 など複数の resource domain がある。今後 Cloudflare 管理対象が増える場合、
`cloudflare-dns` という top-level root 名では用途が狭すぎる。一方で、Cloudflare という product 名だけを
理由に全 resource を一つの Terraform state へまとめると、credential、apply cadence、failure mode、
rollback unit が不必要に結合する。

また、特定 application だけが所有する durable resource と、zone-wide / homecluster-wide resource では
ownership と release lifecycle が異なる。application build artifact や Worker code の deploy を shared
infrastructure state と結合すると、application release と infrastructure lifecycle が混ざる。

## 決定

Cloudflare の shared infrastructure は `terraform/cloudflare/` category 配下に置く。
`terraform/cloudflare/` 自体は Terraform root にせず、用途別の子 directory を独立 root とする。

初期 layout は次とする。

```text
terraform/
├── env/
│   ├── common-crds/
│   ├── common-addons/
│   ├── common-certificates/
│   └── staging/
└── cloudflare/
    ├── README.md
    └── dns/
```

Cloudflare DNS の reusable implementation は `terraform/cloudflare/dns` に置く。DNS root は k3s /
Kubernetes root と state を共有せず、将来追加する別 Cloudflare root とも機械的に state を共有しない。

新しい Cloudflare resource を追加する場合は、次の境界が十分に近い resource だけを同じ root に置く。

- operator / ownership
- credential scope
- apply cadence
- rollback unit
- failure / blast radius

zone-wide / shared infrastructure は `homecluster-infra` の ownership とする。特定 application だけが使う
R2、D1、Vectorize、Queue などの durable resource は application repository 側を ownership 候補とする。
Worker code、Static Assets など application artifact の deploy lifecycle は Terraform state と機械的に
結合しない。

DNS root の既存境界は維持する。

- public source は provider、resource schema、validation、公開可能な運用契約だけを持つ。
- 実 `zone_id`、record name、record content、TTL、proxy policy は private root-specific site input に置く。
- Cloudflare API token は Terraform variable や tfvars に入れず、runtime の
  `CLOUDFLARE_API_TOKEN` から provider へ渡す。
- token は管理対象 zone に限定し、DNS 変更に必要な最小権限だけを与える。
- DNS state と plan は private artifact とし、cluster Terraform state と分離する。
- public CI は format、redaction、source-only validation に限定し、Cloudflare credential を持たず
  `plan` / `apply` を実行しない。
- existing DNS record は最初の managed apply より前に import する。
- live apply controller への統合は source/input contract が成立した後の別変更とする。
- 初期 backend は既存の operator/controller-managed state 境界を使い、R2 backend は採用しない。

初期 DNS record surface は `A`、`AAAA`、`CNAME`、`MX`、`TXT` に限定する。structured data が
必要な record type は provider schema に合わせて明示的に拡張する。

## 理由

repository や product 名より Terraform root / state / credential / ownership 境界の方が、実際の apply
blast radius に直接効く。同じ repository、同じ Cloudflare account にある resource でも state を分ければ、
DNS apply が Tunnel / Access や application data resource の refresh、state lock、rollback を巻き込まない。

`terraform/cloudflare/` という category を設けることで、Cloudflare 関連 source の発見性は保ちつつ、
`cloudflare-dns`、`cloudflare-email`、`cloudflare-access` のような top-level root の増殖を避けられる。

application 専用 resource を application repository 側へ置くことで、shared zone infrastructure と
application release の ownership を明確にできる。一方、DNS zone の ownership は一箇所に固定し、
application ごとに同一 zone の DNS state を分散させない。

API token を runtime environment に限定すると、provider credential と declarative input を分離できる。
zone-scoped token にすることで credential leak 時の account-wide blast radius も抑えられる。

## 不採用案

### Cloudflare resource を一つの Terraform root / state にまとめる

不採用。

同じ provider を使うこと自体は state を共有する理由にならない。DNS、Email、Zero Trust、application data
では credential scope、apply cadence、failure mode、rollback unit が異なる。

### `terraform/env/cloudflare-dns` を維持する

不採用。

`env/` は主に k3s / Kubernetes environment root の文脈で使っており、Cloudflare DNS は staging /
production cluster environment より account / zone infrastructure に近い。また DNS 以外の Cloudflare
resource を追加したときに top-level naming が拡張しにくい。

### Cloudflare 専用 repository を新設する

現時点では不採用。

独立 team、独立 release cadence、独立 access control が必要な規模ではない。category と独立 Terraform
root / state を使えば現在必要な separation は得られる。

### application 専用 Cloudflare resource もすべて `homecluster-infra` に集約する

不採用。

application 専用 durable resource は application code と同じ ownership / lifecycle の方が変更理由を追跡し
やすい。shared DNS zone や homecluster-wide edge policy と application-owned resource を分ける。

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

- Cloudflare shared infrastructure は `terraform/cloudflare/` から発見できる。
- `terraform/cloudflare/` 自体は init / plan / apply しない。
- 現在の DNS root path は `terraform/cloudflare/dns` になる。
- private input workflow は引き続き `cloudflare-dns.tfvars.json` を生成・検証する。
- DNS state / backend identity は path rename を理由に他 root と統合しない。
- Cloudflare provider version と schema は cluster provider とは独立して更新する。
- DNS record の削除は即時の外部影響があるため、将来の controller integration では plan review と
  operator approval を明示 gate にする。
- Email Routing、Zero Trust、Tunnel / Access、WAF / cache などを追加するときは、この ADR の分離基準で
  root を追加または統合する。
- application 専用 Cloudflare durable resource は application repository 側での管理を第一候補とする。

## 見直し条件

- Cloudflare 管理対象が増え、独立 repository / access policy の方が変更境界を明確にできる場合。
- 複数 operator / team が Cloudflare と homecluster cluster infrastructure を別々に管理するようになった場合。
- shared zone infrastructure と application-owned resource の ownership が曖昧になった場合。
- remote state の可用性、versioning、credential separation を評価し、R2 backend の方が既存 backend より
  運用上明確に優れる場合。
- Cloudflare apply を private controller の fixed operation として安定運用できる契約が固まった場合。
