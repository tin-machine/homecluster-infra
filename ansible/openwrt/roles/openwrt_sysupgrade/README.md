# OpenWrt Sysupgrade ロール

OpenWrt ルータのバージョン検出、upgrade 準備、実 sysupgrade を分離します。
`sysupgrade_mode` 未指定時は、互換用の `perform_upgrade` が true なら `upgrade`、それ以外は
`detect` として扱います。

## 変数の注入例（inventory.yml）

```yaml
openwrt:
  hosts:
    router.example:
      openwrt_branch_type: releases
      openwrt_release_version: "24.10.6"
      openwrt_sysupgrade_mode: detect
      openwrt_image_ext: bin
      openwrt_sysupgrade_backup_fetch_enabled: true
      openwrt_sysupgrade_backup_dest: "~/.local/state/homecluster/openwrt-sysupgrade-backups"
      openwrt_sysupgrade_recovery_file_ready: false
      openwrt_sysupgrade_serial_or_usb_recovery_ready: false
      openwrt_sysupgrade_k3s_baseline_ready: false
```

## detect-only 実行例

```console
ansible-playbook -i ../inventory.yml site.yml \
  -l router.example \
  --tags sysupgrade \
  -e openwrt_release_version=24.10.6 \
  -e openwrt_sysupgrade_mode=detect
```

互換用に `openwrt_perform_upgrade=false` でも detect-only を明示できます。

## prepare 実行例

`prepare` は image verify、router 上の backup 作成、control node への fetch、size/SHA256 表示までを
実行し、実 sysupgrade には進みません。

```console
ansible-playbook -i ../inventory.yml site.yml \
  -l router.example \
  --tags sysupgrade \
  -e openwrt_release_version=24.10.6 \
  -e openwrt_sysupgrade_mode=prepare
```

## upgrade 実行例

`upgrade` は recovery readiness と confirmation token が揃った場合だけ `upgrade.yml` へ進みます。

```console
ansible-playbook -i ../inventory.yml site.yml \
  -l router.example \
  --tags sysupgrade \
  -e openwrt_release_version=24.10.6 \
  -e openwrt_sysupgrade_mode=upgrade \
  -e openwrt_sysupgrade_recovery_file_ready=true \
  -e openwrt_sysupgrade_serial_or_usb_recovery_ready=true \
  -e openwrt_sysupgrade_k3s_baseline_ready=true \
  -e openwrt_sysupgrade_confirm='router.example 24.10.6'
```

## 注意事項

- バックアップの tarball は git リポジトリの外に保管してください。
- backup fetch が有効な場合、control node へ取得した tarball の保存先、size、SHA256 を表示します。
- `openwrt_sysupgrade_confirm` は、upgrade mode で `inventory_hostname` と `owrt_upgrade_target` の組み合わせを指定する必要があります。
- `sysupgrade_mode` には `detect`、`prepare`、`upgrade` を指定してください。未指定かつ `openwrt_perform_upgrade` も未指定/false の場合は detect-only です。
- `prepare` モードではバックアップを取得し、ハッシュを表示しますが、実際の sysupgrade は実行しません。
- `upgrade` モードでは、バックアップの取得と、リカバリ準備（`recovery_file_ready`, `serial_or_usb_recovery_ready`, `k3s_baseline_ready`）の確認が完了した後に sysupgrade を実行します。
- ターゲットリリースの変更は、生成された `../inventory.yml` ではなく `homecluster-inventory` ソースで管理してください。
