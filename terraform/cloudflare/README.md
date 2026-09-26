# Cloudflare Terraform

Cloudflare の shared infrastructure を用途別の独立 Terraform root に分けて管理する category です。

この directory 自体は Terraform root ではありません。子 directory ごとに provider、state、credential、
plan / apply lifecycle を分離します。

初期 root:

- `dns/`: zone の DNS record。

将来 Email Routing、Tunnel / Access、WAF / cache などを Terraform 管理へ追加する場合も、Cloudflare という
product 名だけを理由に同じ state へまとめません。次が十分に近い resource だけを同じ root に置きます。

- operator / ownership
- credential scope
- apply cadence
- rollback unit
- failure / blast radius

zone-wide / shared infrastructure はこの repository が所有します。一方、特定 application だけが使う R2、
D1、Vectorize、Queue などの durable resource は application repository 側を ownership 候補とします。
application artifact や Worker code の deploy lifecycle は Terraform state と機械的に結合しません。

public GitHub Actions から Cloudflare の live plan / apply は実行しません。実値、credential、state、plan は
private operator / controller boundary に置きます。
