# cloudflare/worldweaver-hosting

WorldWeaver の production public hostname を、既存の Cloudflare Worker service と既存 R2 bucket へ結び付ける独立 Terraform root です。

この root は **application release bytes を管理しません**。責務は long-lived Cloudflare association に限定します。

~~~text
existing WorldWeaver Worker service
  <- cloudflare_workers_custom_domain
  <- application hostname

existing R2 bucket
  <- cloudflare_r2_custom_domain
  <- immutable asset hostname
~~~

## Ownership boundary

この root が所有するもの:

- Worker Custom Domain association
- R2 Custom Domain association
- Custom Domain lifecycle に付随して Cloudflare が管理する DNS / TLS binding

この root が所有しないもの:

- Worker script / Static Assets release bytes
- Wrangler deploy
- R2 bucket creation
- R2 object upload / delete
- dataset release manifest
- CORS policy / object metadata lifecycle
- generic DNS record for the same hostnames
- GitHub Environment / application deployment credential

WorldWeaver application release は application repository の GitHub Actions + Wrangler が所有します。
R2 の immutable object は content-addressed release workflow が所有します。

同じ hostname を `terraform/cloudflare/dns` の `cloudflare_dns_record` と二重管理してはいけません。Workers Custom Domain は対象 hostname の routing / DNS / certificate lifecycle を Cloudflare 側で管理します。

## Static-first contract

Canonical application policy は WorldWeaver repository の
`docs/cloudflare-static-first-production-policy-2026-10-06.md` にあります。

初期 topology:

~~~text
application hostname
  -> Workers Static Assets
  -> browser WebMCP + DuckDB-Wasm

asset hostname
  -> R2 Custom Domain + CDN
  -> content-addressed immutable WASM / public datasets
~~~

Public REST API / Remote MCP endpoint はこの root の対象外です。

Immutable R2 object は application 側で次のような content-addressed key を使用します。

~~~text
duckdb/<version>/<sha256>/duckdb-*.wasm
datasets/<dataset>/<sha256>/data.parquet
~~~

object metadata の原則:

~~~http
Cache-Control: public, max-age=31536000, immutable
~~~

この Terraform root は object metadata 自体を設定しません。

## Provider

Cloudflare provider は `~> 5.26.0` に固定します。

利用 resource:

- `cloudflare_workers_custom_domain`
- `cloudflare_r2_custom_domain`

provider lock file を commit し、public CI では backend-free `terraform init -lockfile=readonly` + `terraform validate` を行います。

## Private input

実 account / zone / bucket / hostname は外部 input とします。

Shape:

~~~json
{
  "account_id": "<private-account-id>",
  "zone_id": "<private-zone-id>",
  "application_hostname": "app.example.invalid",
  "worker_service_name": "<existing-worker-service>",
  "asset_hostname": "assets.example.invalid",
  "asset_bucket_name": "<existing-r2-bucket>"
}
~~~

実 tfvars、Cloudflare API token、backend endpoint / credential、Terraform state、plan output は public repository に置きません。

Provider authentication は runtime の `CLOUDFLARE_API_TOKEN` を使用します。

## State

DNS / Email Routing と state を共有しません。

候補 state key:

~~~text
cloudflare-worldweaver-hosting/worldweaver.tfstate
~~~

R2 backend bucket、endpoint、credential、state key は controller/operator が `terraform init -backend-config` で注入します。

## Preconditions

最初の live plan より前に次を満たす必要があります。

1. WorldWeaver の validated Worker service が既に存在する。
2. immutable public asset を置く R2 bucket が既に存在する。
3. application hostname と asset hostname が異なる。
4. generic DNS Terraform state が両 hostname を所有していない。
5. Dashboard / Wrangler 等で同じ Custom Domain を別 ownership として管理していない。
6. private input が source revision と対応している。
7. isolated backend state key を使用する。
8. read-only discovery で live association の有無を確認する。

## Initial adoption / activation

この source-only PR 自体は Cloudflare を変更しません。

Cloudflare provider v5.26.0 では、Worker Custom Domain と R2 Custom Domain で adoption capability が異なります。

### Worker Custom Domain

`cloudflare_workers_custom_domain` は provider v5.26.0 で `terraform import` をサポートします。

~~~text
Worker Custom Domain already exists with the intended target
  -> import <account_id>/<domain_id>
  -> refresh plan
  -> expect 0 destroy

Worker Custom Domain does not exist
  -> reviewed create plan
  -> expect exactly the intended association creation
~~~

existing association の domain ID は read-only discovery で確認し、hostname / Worker service / account / zone identity が一致しない場合は停止します。

### R2 Custom Domain

`cloudflare_r2_custom_domain` は provider v5.26.0 で **`terraform import` をサポートしません**。provider の generated resource documentation にも明示されています。

したがって、read-only discovery で対象 R2 Custom Domain が既に存在すると判明した場合、この root で adoption を続行してはいけません。

~~~text
R2 Custom Domain already exists
  -> BLOCKED
  -> do not run create/apply from empty state
  -> do not delete/recreate merely to make Terraform own it
  -> stop until a separately verified and reviewed adoption method exists

R2 Custom Domain does not exist
  -> reviewed create plan
  -> expect exactly the intended association creation
~~~

provider version変更などで正式な import/adoption capability が追加された場合は、provider documentation と live behavior を再検証し、別の reviewed change としてこの停止条件を更新します。未確認の state manipulation を adoption shortcut にしません。

既存 association の identity が曖昧、target Worker / bucket が異なる、generic DNS ownership と重複する場合も停止します。

両 resource に `prevent_destroy = true` を設定し、root / state / hostname の取り違えによる削除を fail closed にします。

## Activation order

推奨する後続フェーズ:

~~~text
1. source-only root merge
2. private input / read-only discovery implementation
3. isolated R2 backend state initialization
4. Worker Custom Domain:
     existing -> supported import
     absent   -> reviewed create plan
5. R2 Custom Domain:
     existing -> BLOCKED until supported adoption is separately verified
     absent   -> reviewed create plan
6. reviewed apply only for resources whose adoption/create path is supported
7. DNS / TLS active state確認
8. WorldWeaver production variablesをcustom hostnameへ切替
9. application smoke test
10. only then automatic production deploy enablement
~~~

Worker / R2 Custom Domain apply と WorldWeaver production traffic enablement を同じ操作にまとめません。
