# cloudflare/dns

Cloudflare の DNS record を管理する独立 Terraform root です。 `terraform/cloudflare/` category の子 root ですが、他の Cloudflare resource と state は共有しません。

この root は k3s / Kubernetes 用 Terraform state と分離します。Cloudflare DNS の変更で cluster provider refresh、cluster state lock、cluster apply を巻き込まないことを目的とします。

## 入力境界

公開 repository には実 zone ID、実 hostname、record content、API token、R2 credential を置きません。

- `zone_id` と `records`: private site input から生成した root-specific `*.tfvars.json`
- Cloudflare API token: runtime environment の `CLOUDFLARE_API_TOKEN`
- R2 credential: runtime environment の `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
- R2 endpoint: runtime environment の `AWS_ENDPOINT_URL_S3`
- R2 bucket と state key: controller / workflow が `terraform init -backend-config` で注入
- plan file: private ephemeral artifact。GitHub artifact や public log へ出さない

credential を Terraform variable や backend configuration fileへ埋め込まない。Cloudflare token は管理対象 DNS zone 群だけに限定し、plan と apply では capability を分ける。

## 対象 record

初期実装は、単純な `content` で安全に表現でき、homecluster で優先度が高い次の type に限定します。

- `A`
- `AAAA`
- `CNAME`
- `MX`
- `TXT`

CAA、SRV、HTTPS、SVCB など structured data を使う record は、必要になった時点で型を拡張します。

`records` は map とし、map key を Terraform 上の安定した identity として使います。同じ owner name に複数 TXT / MX record がある場合でも、別 key で管理できます。

## R2 backend

state は Cloudflare R2 を Terraform の S3-compatible backend として利用します。backend block には S3 compatibility に必要な固定 option だけを置き、bucket、key、endpoint、credential は実行時に注入します。

例:

```bash
export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export AWS_ENDPOINT_URL_S3="https://<account-id>.r2.cloudflarestorage.com"

terraform -chdir=terraform/cloudflare/dns init \
  -reconfigure \
  -input=false \
  -lockfile=readonly \
  -backend-config='bucket=<state-bucket>' \
  -backend-config='key=<zone-state-key>'
```

zone ごとに別の state key を使います。同じ reusable root を利用しても state は共有しません。

R2 の Terraform backend で native S3 lockfile を使う互換性は初期導入時点では前提にしません。GitHub Actions は workflow concurrency で直列化し、CI 実行中に local operator から同じ state を plan/apply しない運用とします。R2 lockfile を採用する場合は別途互換性を acceptance してから `use_lockfile = true` を追加します。

## existing state の移行

既存 local state を R2 へ移すときは、先に local state の private backup を保持し、対象 R2 key が未使用であることを確認します。

backend を R2 向けに init した private work directory から、acceptance 済み local state を明示的に push できます。

```bash
terraform -chdir=terraform/cloudflare/dns state push /private/path/to/accepted.tfstate
terraform -chdir=terraform/cloudflare/dns plan -lock=false -var-file=/private/path/to/site.tfvars.json
```

migration acceptance は既存 state と R2 state の resource identity が一致し、live refresh plan が `No changes` になることです。差分がある場合は apply で合わせず、state/input/live のどこがずれているかを調査します。

## GitHub Actions contract

public `homecluster-infra` repository の Actions は引き続き source-only validation に限定します。実 DNS の desired state は private inventory boundary にあるため、live plan / apply controller は private 側からこの public Terraform root を exact commit SHA で checkout して利用します。

運用 contract:

- pull request: read-only Cloudflare token + read-only R2 credential で `terraform plan -lock=false` のみ
- main: mergeだけでは apply しない
- apply: private workflow の明示的な manual dispatch からのみ実行
- apply job: `cloudflare-production` GitHub Environment の write credential を利用
- Environment required reviewer を利用できる GitHub plan では、それも追加 gate とする
- plan/apply の raw output、plan file、rendered tfvars、state は upload しない
- summary は add/change/destroy 件数だけを残す
- apply 後に再度 plan し、`0 add / 0 change / 0 destroy` を要求する

GitHub Actions credential の詳細は private inventory repository 側で管理し、public repository に secret 名以外の実値や private repository URL を置きません。

## Provider / import

既存 zone の record を Terraform 管理へ移す場合、最初の managed apply より前に既存 record を import します。`cloudflare_dns_record` の import identity は provider v5.25.0 では `<zone_id>/<dns_record_id>` です。

provider lock file は review 済みの `.terraform.lock.hcl` を使い、CI / controller では `terraform init -lockfile=readonly` を要求します。

## State と apply

この root は、`common-crds`、`common-addons`、`common-certificates`、`staging` と state を共有しません。

R2 bucket 自体はこの state から作成しません。backend bootstrap と credential 作成は state の外側で行い、既存 local state を R2 へ移行して no-op acceptance を得てから GitHub Actions の live workflow を有効化します。
