# cloudflare-dns

Cloudflare の DNS record を管理する独立 Terraform root です。

この root は k3s / Kubernetes 用 Terraform state と分離します。Cloudflare DNS の変更で
cluster provider refresh、cluster state lock、cluster apply を巻き込まないことを目的とします。

## 入力境界

公開 repository には実 zone ID、実 hostname、record content、API token を置きません。

- `zone_id` と `records`: private site input から生成した `cloudflare-dns.tfvars.json`
- API token: runtime environment の `CLOUDFLARE_API_TOKEN`
- state / plan: operator または controller が管理する private artifact

API token を Terraform variable にしないため、token 自体を tfvars や provider configuration に
書きません。token は対象 zone に限定し、DNS の変更に必要な最小権限だけを与えます。

## 対象 record

初期実装は、単純な `content` で安全に表現でき、homecluster で優先度が高い次の type に限定します。

- `A`
- `AAAA`
- `CNAME`
- `MX`
- `TXT`

CAA、SRV、HTTPS、SVCB など structured data を使う record は、必要になった時点で型を拡張します。

`records` は map とし、map key を Terraform 上の安定した identity として使います。同じ owner name に
複数 TXT / MX record がある場合でも、別 key で管理できます。

## 実行例

backend path と site input は実行時に指定します。

```bash
terraform -chdir=terraform/env/cloudflare-dns init \
  -backend-config='path=/srv/terraform-state/cloudflare/cloudflare-dns.tfstate'

export CLOUDFLARE_API_TOKEN

terraform -chdir=terraform/env/cloudflare-dns plan \
  -var-file=/path/to/generated/site-inputs/terraform/cloudflare-dns.tfvars.json
```

既存 zone の record を Terraform 管理へ移す場合、最初の apply より前に既存 record を import します。
`cloudflare_dns_record` の import identity は `<zone_id>/<dns_record_id>` です。import せずに既存 record と
同じ name/type/content を作ろうとしないでください。

provider lock file は、review 済み環境で最初の `terraform init` を実行したあとに追加します。それまでは
この root を public CI の `-lockfile=readonly` validation 対象へ入れません。

## State と apply

この root は、`common-crds`、`common-addons`、`common-certificates`、`staging` と state を共有しません。

初期段階では R2 backend を採用しません。state backend を Cloudflare control plane と同時に新設せず、
既存の operator/controller-managed backend 境界を維持します。

public GitHub Actions から Cloudflare へ直接 apply しません。live apply を自動化する場合は、別変更として
private controller boundary と approval / plan / apply contract を設計します。
