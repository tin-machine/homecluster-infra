# cloudflare/zone-redirects

ゾーン単位の Single Redirects を管理する独立した Terraform root です。
Cloudflare provider `~> 5.27.0`、Terraform `>= 1.7.0` を使います。

この root は **ゾーンの `http_request_dynamic_redirect` phase 全体**を所有します。
既存の phase がある場合は停止し、既存ルール全体の所有権と adoption 方法を確認します。
空の state から既存 phase を上書きしたり、既存ルールを黙って削除したりしません。
`prevent_destroy` は置換・削除を防ぎますが、ルール更新のレビューを代替しません。

入力例（実際の入力は private inventory で管理）:

```hcl
zone_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
redirects = {
  apex_to_www = {
    source_hostname = "example.net"
    target_hostname = "www.example.net"
  }
}
```

HTTP と HTTPS の両方で source の完全一致だけを対象にし、HTTPS の target へ301転送します。
パスとクエリ文字列を保持します。map key を固定の rule ref として使い、key 順に生成します。
ホスト名は正規形の小文字 DNS 名に限定し、重複 source、自分自身への転送、転送の連鎖・循環を拒否します。

転送元の proxied DNS レコードは別の generic DNS root が管理します。
この root は DNS、Email Routing、Workers、R2 を変更しません。
backend の bucket・key と認証情報は実行側から渡し、source に保存しません。

API・backend に接続しない検証:

```bash
terraform init -backend=false -input=false -lockfile=readonly
terraform validate
terraform test -no-color
```

テストは mock provider を使います。本番への `plan`・`apply` を意味しません。
