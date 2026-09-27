# Cloudflare Terraform

Cloudflare の shared infrastructure を用途別の独立 Terraform root に分けて管理する category です。

この directory 自体は Terraform root ではありません。子 directory ごとに provider、state、credential、plan / apply lifecycle を分離します。

初期 root:

- `dns/`: zone の DNS record。

将来 Email Routing、Tunnel / Access、WAF / cache などを Terraform 管理へ追加する場合も、Cloudflare という product 名だけを理由に同じ state へまとめません。次が十分に近い resource だけを同じ root に置きます。

- operator / ownership
- credential scope
- apply cadence
- rollback unit
- failure / blast radius

zone-wide / shared infrastructure はこの repository が reusable implementation を所有します。一方、実 zone ID、record content、credential、state は private boundary に置きます。特定 application だけが使う R2、D1、Vectorize、Queue などの durable resource は application repository 側を ownership 候補とします。application artifact や Worker code の deploy lifecycle は Terraform state と機械的に結合しません。

## GitHub Actions と state

`terraform/cloudflare/dns` は Cloudflare R2 を S3-compatible remote backend として使います。public source には R2 bucket、state key、account endpoint、credential を固定せず、controller / workflow が runtime に注入します。

public `homecluster-infra` Actions は実 site input や Cloudflare credential を持たず、source-only validation の境界を維持します。live Cloudflare DNS plan / apply は private inventory boundary から、この public root の exact commit SHA を利用して行います。

DNS live workflow は次を前提にします。

- PR は read-only credential で plan のみ。
- main mergeだけでは apply しない。
- apply は明示的な manual dispatch と `cloudflare-production` GitHub Environment を通す。
- GitHub plan が private repository の required reviewer を提供する場合は Environment approval も追加する。
- state は R2 で zone ごとに key を分離する。
- raw plan、rendered input、state は artifact / log に公開しない。
- workflow concurrency で同じ DNS state の CI 実行を直列化する。

家庭内 LAN / k3s / router へ public GitHub Actions から到達させない ADR 0009 の境界は変更しません。Cloudflare DNS は外部 SaaS control plane に限定した別 lifecycle とします。
