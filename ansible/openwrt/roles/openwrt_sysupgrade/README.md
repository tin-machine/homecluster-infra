# OpenWrt Sysupgrade ロール

OpenWrt ルータのバージョン検出と sysupgrade 実行を分離し、デフォルトでは検出のみを行います。`perform_upgrade: true` を指定したホストだけが実際の sysupgrade を実施します。

## 変数の注入例（inventory.yml）

```yaml
openwrt:
  hosts:
    router.example:
      openwrt_branch_type: releases
      openwrt_release_version: "24.10.0"
      openwrt_perform_upgrade: true
      openwrt_image_ext: itb
```

## TODO

- `sysupgrade -b` で作成した `backup-*.tar.gz` を自動で取得・保管する仕組み（`fetch` など）を追加する。
