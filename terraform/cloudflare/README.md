# Cloudflare Terraform

Cloudflare の shared infrastructure を用途別の独立 Terraform root に分けて管理する category です。

この directory 自体は Terraform root ではありません。子 directory ごとに provider、state、credential、plan / apply lifecycle を分離します。

現在の root:

- `dns/`: zone の generic DNS record。
- `email-routing/`: Email Routing DNS / rule / catch-all lifecycle。
- `worldweaver-hosting/`: WorldWeaver の Worker Custom Domain と既存 R2 bucket の Custom Domain association。Worker release bytes と R2 object bytes は管理しない。

将来 Tunnel / Access、WAF / cache などを Terraform 管理へ追加する場合も、Cloudflare という product 名だけを理由に同じ state へまとめません。次が十分に近い resource だけを同じ root に置きます。

- operator / ownership
- credential scope
- apply cadence
- rollback unit
- failure / blast radius

zone-wide / shared infrastructure はこの repository が reusable implementation を所有します。一方、実 zone ID、record content、credential、state は private boundary に置きます。特定 application だけが使う R2、D1、Vectorize、Queue などの durable resource 本体は application repository 側を ownership 候補とします。application artifact や Worker code の deploy lifecycle は Terraform state と機械的に結合しません。

WorldWeaver ではこの境界をさらに分離し、Worker script / Static Assets release と R2 object bytes は `worldweaver` 側、public hostname を long-lived Cloudflare resource へ結び付ける Worker / R2 Custom Domain association は `worldweaver-hosting/` root が所有します。Custom Domain が生成・管理する DNS record を generic `dns/` root で二重所有しません。

## GitHub Actions と state

`terraform/cloudflare/dns` は Cloudflare R2 を S3-compatible remote backend として使います。public source には R2 bucket、state key、account endpoint、credential を固定せず、controller / workflow が runtime に注入します。

public `homecluster-infra` Actions は実 site input や Cloudflare credential を持たず、source-only validation の境界を維持します。実 DNS の desired state は private inventory boundary が所有し、live Cloudflare DNS plan / apply は private apply-controller boundary が inventory とこの public root の exact commit SHA を組み合わせて実行します。credential と GitHub Actions workflow も private apply-controller boundary が所有します。

DNS live workflow は次を前提にします。

- PR は read-only credential で plan のみ。
- main mergeだけでは apply しない。
- apply は明示的な manual dispatch と `cloudflare-production` GitHub Environment を通す。
- GitHub plan が private repository の required reviewer を提供する場合は Environment approval も追加する。
- state は R2 で zone ごとに key を分離する。
- raw plan、rendered input、state は artifact / log に公開しない。
- workflow concurrency で同じ DNS state の CI 実行を直列化する。

家庭内 LAN / k3s / router へ public GitHub Actions から到達させない ADR 0009 の境界は変更しません。Cloudflare DNS は外部 SaaS control plane に限定した別 lifecycle とします。
