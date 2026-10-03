# cloudflare/email-routing

Cloudflare Email Routing を DNS record lifecycle から分離して管理する独立 Terraform root です。

この root は `terraform/cloudflare/dns` と state、credential、ownership を共有しません。実 zone ID、zone name、routing address、destination address、API token、R2 backend value は public repository に置きません。

## Initial authority

Phase 8B の初期 authority は次に限定します。

- `cloudflare_email_routing_dns`: Email Routing enablement と Cloudflare-owned MX/SPF lifecycle
- `cloudflare_email_routing_rule`: explicit literal/to rule
- `cloudflare_email_routing_catch_all`: catch-all rule

`cloudflare_email_routing_address` は管理しません。destination address の登録、削除、verification は external operator prerequisite です。

`cloudflare_email_routing_settings` も初期 managed resource にはしません。provider 5.26.0 は `support_subaddress` schema を追加しましたが、resource implementation は update を実装しておらず、Create/Delete は deprecated enable/disable API を使用します。Email Routing の enablement authority は正式な DNS endpoint を使う `cloudflare_email_routing_dns` に一本化します。settings/status/support_subaddress は当面 read-only inventory で観測します。

## Provider

この root は Cloudflare provider `~> 5.26.0` を使います。

5.26.0 を最低線にする理由は、5.23-5.25 系で `cloudflare_email_routing_settings.support_subaddress` の model/schema mismatch があり、5.26.0 で resource schema が修正されたためです。

ただし provider 側には `cloudflare_email_routing_rule` / `cloudflare_email_routing_catch_all` の deprecated computed `tag` が plan ごとに unknown へ戻り、tag-only in-place update を提案する既知 issue があるため、live adoption では必ず再現有無を確認します。tag-only drift が出た場合、apply を繰り返して収束させようとせず provider blocker として扱います。

provider lock file は live adoption 前に `terraform providers lock` で生成し、review 済みの lock file を commit してから `terraform init -lockfile=readonly` を要求します。Phase 8B source-foundation PR では provider schema/authority の review を先に行い、lock file 生成は local validation checkpoint に残します。

## Private input

想定する `tfvars.json` shape:

```json
{
  "zone_id": "<private-zone-id>",
  "zone_name": "<private-zone-name>",
  "rules": {
    "<stable-private-identity>": {
      "name": "<private-rule-name>",
      "enabled": true,
      "priority": 0,
      "matcher": {
        "type": "literal",
        "field": "to",
        "value": "<private-recipient-address>"
      },
      "action": {
        "type": "forward",
        "value": ["<already-verified-destination-address>"]
      }
    }
  },
  "catch_all": {
    "name": "<private-catch-all-name>",
    "enabled": false,
    "action": {
      "type": "drop"
    }
  }
}
```

Private input must come from `homecluster-inventory` SOPS source. Raw tfvars and plan output are private artifacts and must not be uploaded or printed.

## State

Zone ごとに別 workdir / backend state key を使用します。

候補:

```text
cloudflare-email-routing/selfemo-dev.tfstate
cloudflare-email-routing/chill-out-dev.tfstate
```

R2 backend bucket、endpoint、credential、state key は controller/operator が `terraform init -backend-config` で注入します。

`cloudflare_email_routing_dns.zone` には `prevent_destroy = true` を設定しています。resource removal や state/root の取り違えによって Email Routing DNS を disable する事故を fail closed にします。

## Adoption

既存 live state を apply で desired state に寄せません。最初の managed mutation より前に identity を照合して import します。

Import identities:

```text
cloudflare_email_routing_dns.zone
  <zone_id>

cloudflare_email_routing_rule.rule["<stable-identity>"]
  <zone_id>/<rule_identifier>

cloudflare_email_routing_catch_all.zone
  <zone_id>
```

`cloudflare_email_routing_settings` はこの root へ import しません。

Adoption gate:

1. Phase 8A read-only inventory accepted.
2. verified destination address exists externally.
3. generic DNS ownership overlap is zero.
4. private input validates without printing values.
5. provider lock file is generated and reviewed.
6. isolated zone-specific workdir and state key.
7. existing resources that this root will own are imported before apply.
8. refresh/live plan is reviewed.
9. destroy count must be zero.
10. provider tag-only drift is classified separately and must not trigger a blind apply.
11. only after provider behavior is accepted may a mutation path be designed.

Phase 8B itself does not enable GitHub Actions apply.
