# OpenWrt Sysupgrade ロール

OpenWrt ルータのバージョン検出と sysupgrade 実行を分離し、デフォルトでは検出のみを行います。`perform_upgrade: true` を指定したホストだけが実際の sysupgrade を実施します。

## 変数の注入例（inventory.yml）

```yaml
openwrt:
  hosts:
    router.example:
      openwrt_branch_type: releases
      openwrt_release_version: "24.10.6"
      openwrt_perform_upgrade: true
      openwrt_image_ext: bin
      openwrt_sysupgrade_backup_fetch_enabled: true
      openwrt_sysupgrade_backup_dest: "~/.local/state/homecluster/openwrt-sysupgrade-backups"
      openwrt_sysupgrade_confirm: "router.example 24.10.6"
```

## 実行例

```console
ansible-playbook -i ../inventory.yml site.yml \
  -l router.example \
  --tags sysupgrade \
  -e openwrt_release_version=24.10.6 \
  -e openwrt_perform_upgrade=true \
  -e openwrt_sysupgrade_confirm='router.example 24.10.6'
```

## 注意事項

- バックアップの tarball は git リポジトリの外に保管してください。
- backup fetch が有効な場合、control node へ取得した tarball の保存先、size、SHA256 を表示します。
- `openwrt_sysupgrade_confirm` は、`inventory_hostname` と `owrt_upgrade_target` の組み合わせを指定する必要があります。
