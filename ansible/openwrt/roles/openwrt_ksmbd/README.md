# openwrt_ksmbd

OpenWrt 上の authenticated ksmbd share を管理する role です。public default は無効で、share、account、password、firewall opt-in は外部 inventory から注入します。

## 必須入力

```yaml
openwrt_ksmbd_enabled: true
openwrt_firewall_allow_smb: true
openwrt_ksmbd_users:
  - name: example-user
    password: "<external-secret>"
openwrt_ksmbd_shares:
  - name: example-share
    path: /srv/example-share
    read_only: "no"
    guest_ok: "no"
    users: example-user
    write_list: example-user
    browseable: "yes"
```

password は repository、CI log、runbookへ記録しません。share pathは事前に別のstorage roleで作成し、このroleは存在確認してから `/etc/config/ksmbd`、account、service、TCP/445 listenerを管理します。

## 実行入口

```bash
ansible-playbook -i ../inventory.yml ansible/openwrt/site.yml \
  --limit openwrt \
  --tags ksmbd
```

`ksmbd` tagはfirewall roleも実行します。WAN公開、TCP/139、guest share、`wsdd2` は追加しません。
