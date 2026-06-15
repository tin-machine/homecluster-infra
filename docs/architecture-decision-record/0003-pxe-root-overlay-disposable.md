---
status: accepted
audience: human-ai
scope: pxe-root-overlay
last_reviewed: 2026-05-31
---

# ADR 0003: PXE root overlay は disposable にする

## 状況

PXE boot した Gentoo node は、NFS rootfs を共有 lower として使う。各 node が root filesystem へ直接永続書き込みをすると、状態が散らばり、rootfs refresh、rollback、troubleshooting が難しくなる。

一方で、boot 後には package install、service state、temporary file などの書き込みが発生する。rootfs を完全 read-only にするだけでは通常運用に耐えない。

## 決定

PXE root は NFS lower + tmpfs upper/work の [overlayfs dracut module](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/dracut/70overlayfs/mount-overlayfs.sh.j2) として扱う。

- lower: NFS rootfs。
- upper: PXE client local tmpfs。
- work: PXE client local tmpfs。
- 差分: reboot で破棄する。

root overlay は disposable とし、再起動後に残すべき state は root filesystem ではなく、明示した外部 storage、local block backed filesystem、または configuration management 側へ置く。

boot 後に package install や source build が走る場合、収束時間は長くなり得る。それでも rootfs に手動差分を残さず、clean boot から configuration management で再収束できることを優先する。

## 理由

node ごとの差分を reboot で捨てられるため、PXE rootfs の再現性が高くなる。

NFS lower を clean build / promote / rollback する運用と相性がよい。

rootfs 側に状態を残さない前提にすると、k3s runtime、application state、cache の置き場所を明示的に設計できる。

build 時間が問題になる場合は、手動変更を root overlay へ残すのではなく、NFS lower rootfs への事前導入、[release bundle](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/release_bundle.yml)、[binary package cache](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/portage/binpkg.conf.j2)、または明示 gate 付きの prebuild として扱う。これにより reboot convergence の再現性を崩さずに収束時間だけを短縮できる。

## 不採用案

### NFS rootfs へ直接書き込む

不採用。

複数 node が同じ rootfs を変更し、rootfs refresh と rollback の境界が崩れる。

### remote upper/work を使って差分を永続化する

不採用。

永続差分は一見便利だが、lower rootfs の世代更新時に差分との整合性を追う必要がある。家庭内検証環境では、状態を捨てて再収束する方が単純である。

### root overlay を使わず、node ごとに full rootfs を持つ

不採用。

node ごとの rootfs 管理が増え、PXE / NFS rootfs の central management の利点が小さくなる。

## 影響

boot 後に入れた package や root filesystem 上の手動変更は reboot で消える。

k3s runtime、container image store、database、installer destination など local filesystem semantics が必要な path は [k3s local storage role](../../ansible/arm64/roles/k3s_local_storage/tasks/main.yml) 側の storage policy に従う。

## 見直し条件

- root overlay の tmpfs 容量が恒常的に不足し、local block backed upper を使う方が単純になった場合。
- node ごとの差分を長期間維持する明確な要件が出た場合。
