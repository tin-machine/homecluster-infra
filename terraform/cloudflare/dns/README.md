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

terraform -chdir="<zone-specific-private-workdir>" init \
  -reconfigure \
  -input=false \
  -lockfile=readonly \
  -backend-config='bucket=<state-bucket>' \
  -backend-config='key=<zone-state-key>'
```

zone ごとに別の state key と別の private work directory を使います。同じ reusable root を利用しても state と backend metadata は共有しません。

R2 の Terraform backend で native S3 lockfile を使う互換性は初期導入時点では前提にしません。GitHub Actions は workflow concurrency で直列化し、CI 実行中に local operator から同じ state を plan/apply しない運用とします。R2 lockfile を採用する場合は別途互換性を acceptance してから `use_lockfile = true` を追加します。

## local state から R2 への cutover

この backend 変更を main へ merge する前に、Terraform 管理済みの全 zone を R2 へ移行し、zone ごとの no-op acceptance を完了します。merge 自体を local backend から R2 backend への運用 cutover gate とします。

R2 migration 中は main の既存 local-backend configuration が現行運用の正本です。migration 作業だけ、PR head の `terraform/cloudflare/dns` を exact commit SHA で取得して zone ごとの isolated private work directory に複製して使います。同じ work directory を複数 zone で再利用しません。

zone ごとの手順:

1. acceptance 済み local state の private backup と resource identity を確認する。
2. 対象 R2 state key が未使用であることを確認する。
3. PR head の Terraform root を、その zone 専用の private work directory へ配置する。
4. その directory だけを対象 R2 key へ `terraform init` する。
5. push 前に local state の zone ID、resource address、provider resource ID と対象 zone / R2 key の対応を private 側で照合する。
6. acceptance 済み local state を現在設定された R2 backend へ pushする。
7. R2 から state を再取得し、resource identity が local accepted state と一致することを確認する。
8. 同じ zone の rendered input で live refresh plan を実行し、`0 add / 0 change / 0 destroy` を要求する。
9. acceptance を記録してから次の zone へ進む。

概念例:

```bash
zone_workdir="<private-work-root>/<fixed-zone-alias>"
accepted_state="<private-accepted-state>"
site_tfvars="<private-rendered-tfvars>"

terraform -chdir="${zone_workdir}" init \
  -reconfigure \
  -input=false \
  -lockfile=readonly \
  -backend-config='bucket=<state-bucket>' \
  -backend-config='key=<zone-specific-state-key>'

# push 前に destination が未使用であることと、
# accepted state の zone/resource identity を private 側で照合する。
terraform -chdir="${zone_workdir}" state push "${accepted_state}"

terraform -chdir="${zone_workdir}" plan \
  -lock=false \
  -var-file="${site_tfvars}"
```

raw state、record content、zone ID、resource ID は public log に出しません。差分がある場合は apply で合わせず、state/input/live のどこがずれているかを調査します。

すべての対象 zone で R2 state と live refresh plan の acceptance が完了するまで、この backend 変更を main へ mergeしません。merge 後の live plan / apply は R2 backend を前提とし、旧 local state は rollback / audit evidence として保持します。

緊急に pre-R2 local state を再確認する必要がある場合は、最後に acceptance 済みだった pre-R2 exact commit SHA を isolated private work directory で使います。merge 後の R2-configured rootに対して `init -backend=false` を行い、そのまま旧 local stateで live planできるものとは扱いません。

## GitHub Actions contract

public `homecluster-infra` repository の Actions は引き続き source-only validation に限定します。実 DNS の desired state は private inventory boundary が所有し、live plan / apply は private `homecluster-apply-controller` が inventory とこの public Terraform root の exact commit SHA を組み合わせて実行します。

運用 contract:

- private controller の plan workflow: read-only Cloudflare token + read-only R2 credential で `terraform plan -lock=false` のみ
- main: mergeだけでは apply しない
- apply: private workflow の明示的な manual dispatch からのみ実行
- apply job: `cloudflare-production` GitHub Environment の write credential を利用
- Environment required reviewer を利用できる GitHub plan では、それも追加 gate とする
- plan/apply の raw output、plan file、rendered tfvars、state は upload しない
- summary は add/change/destroy 件数だけを残す
- apply 後に再度 plan し、`0 add / 0 change / 0 destroy` を要求する

GitHub Actions workflow、Environment Secret、Cloudflare / R2 credential は private `homecluster-apply-controller` 側で管理します。private inventory repository は desired state の owner とし、execution credential は置きません。public repository には secret 名以外の実値や private repository URL を置きません。

## Provider / import

既存 zone の record を Terraform 管理へ移す場合、最初の managed apply より前に既存 record を import します。`cloudflare_dns_record` の import identity は provider v5.25.0 では `<zone_id>/<dns_record_id>` です。

provider lock file は review 済みの `.terraform.lock.hcl` を使い、CI / controller では `terraform init -lockfile=readonly` を要求します。

## State と apply

この root は、`common-crds`、`common-addons`、`common-certificates`、`staging` と state を共有しません。

R2 bucket 自体はこの state から作成しません。backend bootstrap と credential 作成は state の外側で行い、既存 local state を R2 へ移行して no-op acceptance を得てから GitHub Actions の live workflow を有効化します。
