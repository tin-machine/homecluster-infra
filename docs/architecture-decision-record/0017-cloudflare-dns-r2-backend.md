---
status: accepted
audience: human-ai
scope: cloudflare-dns-r2-backend
last_reviewed: 2026-09-27
---

# ADR 0017: Cloudflare DNS state を R2 remote backend へ移行する

## 状況

Cloudflare DNS の初回 Terraform 移管では、DNS migration、provider import、credential、
remote backend migration を同時に導入しないため、zone ごとの private local state を使用した。

その後、複数 zone で existing record の import と no-op acceptance が完了し、DNS root の
source / input / state separation が成立した。次の段階では、operator host に閉じた local state ではなく、
private controller から再現可能に plan / apply できる remote state が必要になる。

Cloudflare R2 は S3-compatible API を提供しており、Cloudflare DNS と同じ external control plane から
到達できる。家庭内 LAN や k3s cluster への接続を必要とせず、Terraform の S3 backend として利用できる。

ADR 0016 では初期 migration の安全性を優先し R2 backend を採用しなかったが、その migration phase は
完了したため、この ADR で backend 方針を更新する。

## 決定

`terraform/cloudflare/dns` は Cloudflare R2 を S3-compatible remote backend として使用する。

public source の backend blockには R2 compatibility に必要な固定 optionだけを置く。
次の実値は repositoryへ固定しない。

- R2 account endpoint
- bucket name
- state key
- access key ID / secret access key

これらは private controller / workflow の runtime configurationから注入する。

同じ reusable Terraform rootを使用しても、zoneごとに state keyを分離する。zone間でstate、
rollback unit、apply failureを共有しない。

既存 local stateは作り直さず、zoneごとに R2へ移行する。migration acceptanceは、
R2 stateのresource identityが既存 accepted stateと一致し、live refresh planが
`0 add / 0 change / 0 destroy` になることとする。state migrationとDNS mutationを同じ操作にしない。

live plan / apply は public repository の GitHub Actionsから実行しない。real site inputとcredentialを扱う
private controller boundaryが public Terraform rootを exact commit SHAで取得して実行する。

initial R2 adoptionでは S3 native lockfile compatibilityを前提にしない。controller / workflow側の
concurrencyで同一DNS stateへの実行を直列化し、同一stateに対するlocal plan/applyとの同時実行を禁止する。
R2でnative lockfileを採用する場合は、別途互換性を検証してから有効化する。

## 理由

remote stateにより、特定operator hostのlocal filesystemへstate ownershipを固定せず、
private controllerから同じaccepted stateを利用できる。

R2を使うことで、Cloudflare DNSのTerraform実行に家庭内network到達性を要求せず、
GitHub-hosted execution environmentからもexternal control planeだけで完結できる。

bucket / key / credentialをruntime injectionとすることで、public reusable sourceとprivate site identityを
分離した既存contractを維持できる。

zoneごとのstate key分離は、一つのzoneの変更・失敗・rollbackが別zoneを巻き込むことを防ぐ。

## 不採用案

### local stateを恒久運用する

不採用。

operator hostにstate ownershipが固定され、private controller / automationから同じstateを安全に再利用しにくい。

### 全zoneを一つのTerraform stateへまとめる

不採用。

zoneごとのapply cadence、failure、rollback unitを不必要に結合する。

### R2 bucketを同じDNS Terraform rootで作成する

不採用。

backendが存在しないとstateを初期化できないためbootstrap dependencyが循環する。backend bucketとcredentialは
state外の独立operationとして作成する。

### public GitHub Actionsへlive credentialを置く

不採用。

public source review surfaceとproduction credential利用surfaceを直接接続しない既存境界を維持する。

### R2 native lockfileを未検証のまま有効にする

不採用。

S3-compatible storageでのlock semanticsを推測でproduction contractへ入れない。初期導入はcontroller側の
serializationで安全側に倒し、native lockは別acceptanceとする。

## 影響

- `terraform/cloudflare/dns/providers.tf` に S3 backend blockを持つ。
- local-only initでは backendを明示的に無効化するか、private backend configurationを渡す必要がある。
- provider credentialとR2 credentialは別物として扱う。
- state migration前のaccepted local stateは、移行確認が完了するまでprivate rollback evidenceとして保持する。
- private controllerはzone aliasからstate keyを固定的に解決し、free-form state keyをapply入力にしない。
- raw state、saved plan、rendered real tfvarsをpublic artifactへ出さない。
- ADR 0016 の「初期 state backendをR2にしない」というmigration-phase判断は、このADRによって後続運用では更新される。

## 見直し条件

- R2 backendの可用性やTerraform compatibilityに運用上の問題が見つかった場合。
- TerraformがS3-compatible backend向けに新しいlocking mechanismを提供し、R2でacceptanceできた場合。
- Cloudflare DNSのstate ownershipやcredential boundaryを別control planeへ移す場合。
