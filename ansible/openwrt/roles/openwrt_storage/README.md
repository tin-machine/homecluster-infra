# OpenWrt Storage ロール

OpenWrt ルーターに接続されたストレージを PXE 用に初期化し、`/etc/config/fstab` を管理するロールです。

既定値は historical な FAT32 500MiB + f2fs 構成を維持しています。新規ディスク移行では `openwrt_storage_partition_map` を使い、実機固有の layout を明示します。

## 機能

- ストレージ操作で必要となるパッケージ（`fdisk`, `parted`, `block-mount`, `f2fs-tools`, `dosfstools`, `kmod-fs-f2fs`, `kmod-fs-vfat`, `kmod-nls-*`）の導入
- legacy mode では `/dev/sda1` を FAT32、`/dev/sda2` を f2fs として扱う
- `openwrt_storage_partition_map` 指定時は partition 番号、start/end、fstype、label、mount target を明示できる
- destructive 操作は `force_*` だけでなく `openwrt_storage_destructive_confirm=erase-<device>` と model/serial guard で保護する
- swap は `openwrt_storage_swap_device: auto` で `TYPE="swap"` から autodetect できる
- `block mount` を実行して即座にマウントを反映（任意）

## 主要変数

| 変数 | デフォルト | 説明 |
| --- | --- | --- |
| `openwrt_storage_device` | `/dev/sda` | 操作対象のブロックデバイス |
| `openwrt_storage_boot_size_mib` | `500` | 先頭 FAT32 パーティションのサイズ (MiB) |
| `openwrt_storage_boot_mount` | `/srv/boot` | FAT32 をマウントするパス |
| `openwrt_storage_data_mount` | `/srv` | f2fs をマウントするパス |
| `openwrt_storage_force_repartition` | `false` | `true` の場合 GPT を再作成しパーティションを切り直す（破壊的） |
| `openwrt_storage_force_format` | `false` | 既存パーティションは維持しつつ再フォーマットする |
| `openwrt_storage_destructive_confirm` | `""` | 破壊的操作時に必要な確認 token。例: `erase-nvme0n1` |
| `openwrt_storage_expected_model` | `""` | 破壊的操作時の model guard |
| `openwrt_storage_expected_serial` | `""` | 破壊的操作時の serial guard |
| `openwrt_storage_partition_map` | `[]` | 空なら legacy layout。指定時は任意 layout を使う |
| `openwrt_storage_manage_fstab` | `true` | `false` の場合、partition/format 準備だけ行い fstab は切り替えない |
| `openwrt_storage_swap_identifier_mode` | `auto` | `auto`, `uuid`, `label`, `device` |
| `openwrt_storage_apply_mounts` | `true` | `block mount` を実行するかどうか |
| `openwrt_storage_swap_device` | なし | 指定時に `/etc/config/fstab` の swap セクションを作成（例: `/dev/sdb2`、または swap が 1 個だけの環境では `auto`） |

## 使い方

### site.yml

```yaml
- hosts: openwrt
  gather_facts: false
  roles:
    - bootstrap_python
    - { role: openwrt_storage, tags: ['openwrt_storage'] }
    # 以降のロール…
```

タグを付与しているため、以下のように部分適用が可能です。

```bash
ansible-playbook -i inventory.yml site.yml --tags openwrt_storage
```

### legacy 初期セットアップ例（force オプション）

```yaml
openwrt_storage_force_repartition: true
openwrt_storage_force_format: true
openwrt_storage_device: /dev/sda
openwrt_storage_boot_label: OWRT_BOOT
openwrt_storage_data_label: GENTOO_SRV
openwrt_storage_swap_device: /dev/sdb2
```

### OpenWrt NVMe 移行例（8GiB swap + f2fs /srv）

2026-06-07 時点の OpenWrt 内部 NVMe 候補に対する layout 例です。`/srv/boot` と `/srv/openwrt-backups` は作りません。OpenWrt の FAT32 指定は USB recovery / upgrade media の条件であり、NVMe 上の `/srv` layout 要件ではありません。

```yaml
openwrt_enable_storage: true
openwrt_storage_device: /dev/nvme0n1
openwrt_storage_expected_model: "Micron MTFDKCD512TFK"
openwrt_storage_expected_serial: "22173743B91B"
openwrt_storage_destructive_confirm: erase-nvme0n1
openwrt_storage_force_repartition: true
openwrt_storage_force_format: true
openwrt_storage_manage_fstab: false
openwrt_storage_apply_mounts: false
openwrt_storage_swap_device: auto
openwrt_storage_partition_map:
  - number: 1
    start: 1MiB
    end: 8193MiB
    fstype: swap
    parted_type: linux-swap
    label: OWRT_SWAP
  - number: 2
    start: 8193MiB
    end: 100%
    fstype: f2fs
    label: GENTOO_SRV_NEW
    target: /srv
    fstab_section: srv
    mount_options: rw,noatime,noacl
    mount_mode: '0755'
```

`manage_fstab=false` は「新ディスクを作るだけ」の段階で使います。`/srv` copy と rollback evidence が揃った後に、同じ partition map で `manage_fstab=true`、`force_repartition=false`、`force_format=false` にして fstab 切替を行います。swap は `openwrt_storage_swap_device: auto` により `TYPE="swap"` の autodetect 結果から選びます。partition map に swap が含まれる場合は、その map 内の swap partition に候補を絞ります。

> **注意:** `openwrt_storage_force_repartition` / `openwrt_storage_force_format` はすべてのデータを消去します。通常運用では `false` のままにしてください。

## デバイス確認

OpenWrt 再起動後は USB storage の列挙順が変わり、`/dev/sda` / `/dev/sdb` が入れ替わることがあります。通常運用では `/dev/sdX` を正とせず、LABEL/UUID/TYPE と mount 先で確認します。

```bash
ROUTER=router.example
ROUTER_IP="$(ansible-inventory -i inventory.yml --host "$ROUTER" | jq -r '.openwrt_lan_ipaddr // .ansible_host')"

ssh "root@${ROUTER_IP}" 'block info /dev/sd* /dev/mmcblk* 2>/dev/null || true'
ssh "root@${ROUTER_IP}" 'mount | grep -E " on (/srv|/srv/boot|/srv/shared-data|/mnt/shared-data)( |$)"'
ssh "root@${ROUTER_IP}" 'cat /proc/swaps'
ssh "root@${ROUTER_IP}" 'uci show fstab | grep -E "(srv|srv_boot|swap0|external_disk)"'
```

期待値:

- `LABEL="GENTOO_SRV"` / `TYPE="f2fs"` が `/srv`
- NVMe 移行後は `LABEL="OWRT_SWAP"` / `TYPE="swap"` が 8GiB partition
- `fstab.srv.uuid` は UUID 指定
- `fstab.swap0` は `openwrt_storage_swap_device: auto` による autodetect 結果を使う

## TODO

- `partprobe` 未導入環境での再認識処理（`block detect` など）も検討する。
- f2fs チューニングオプション（`background_gc` や `alloc_mode` など）の変数化。
