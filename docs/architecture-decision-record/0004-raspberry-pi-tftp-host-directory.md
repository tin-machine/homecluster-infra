---
status: accepted
audience: human-ai
scope: raspberry-pi-tftp-host-directory
last_reviewed: 2026-05-31
---

# ADR 0004: Raspberry Pi TFTP は board hash 実 directory を優先する

## 状況

Raspberry Pi の network boot は、一般的な PXE boot loader と異なり、firmware が board 固有 path を探索する。host ごとに boot artifact や `cmdline.txt` を切り替えるには、この探索順に合わせた TFTP layout が必要になる。

TFTP root 外への symlink や抽象化した共通 path は、TFTP server や firmware の挙動によって file not found になり得る。

## 決定

Raspberry Pi TFTP は、board hash の実 directory を host directory として扱う。

host directory には、boot に必要な file または TFTP root 内を指す symlink を置く。TFTP root 外への symlink を前提にしない。

host ごとの rootfs release / role / stage は、[cmdline.txt template](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/cmdline.txt.j2) と DHCP metadata で制御する。

## 理由

Raspberry Pi firmware が実際に読む path に合わせることで、boot 失敗時の確認対象が単純になる。

TFTP root 内に閉じた layout にすると、TFTP server の chroot / symlink 制約に左右されにくい。

host directory 単位で `cmdline.txt` を切り替えられるため、canary / rollback / staged release の操作を host 単位で行える。

## 不採用案

### TFTP root 外への symlink を使う

不採用。

TFTP server が root 外 symlink を解決できない場合があり、firmware 側からは単に file not found に見える。障害時の切り分け負荷が高い。

### 全 host が共通 boot directory だけを読む

不採用。

host ごとの release 切替や rollback の境界が弱くなる。Raspberry Pi 世代差や host 個別の boot requirement を扱いにくい。

### SD card boot を標準にする

不採用。

[SD-assisted boot image role](../../ansible/openwrt/roles/rpi_sd_netboot_image/tasks/main.yml) は有用な fallback / variant だが、通常運用では TFTP artifact を中央管理できる PXE/TFTP を優先する。

## 影響

新しい Raspberry Pi を追加するときは、最初に board hash / TFTP prefix を採取する必要がある。

[board hash directory の作成、release symlink、`cmdline.txt` 生成](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/tftp_hosts.yml)を Ansible の管理対象にする。

## 見直し条件

- 未登録 Raspberry Pi onboarding を自動化し、generic boot directory の方が安全に扱えると確認できた場合。
- Pi firmware / TFTP server の仕様変更により、TFTP root 外 symlink を安全に扱える条件が明確になった場合。
