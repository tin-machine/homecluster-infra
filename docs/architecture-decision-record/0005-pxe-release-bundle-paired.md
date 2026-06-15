---
status: accepted
audience: human-ai
scope: pxe-release-bundle
last_reviewed: 2026-05-31
---

# ADR 0005: PXE release は TFTP artifact と NFS rootfs を paired bundle として扱う

## 状況

PXE boot では、TFTP artifact と NFS rootfs の組み合わせが一致していないと、kernel / DTB / module / initramfs / rootfs の対応が崩れる。

TFTP artifact だけ新しい、または rootfs だけ新しい状態が残ると、boot failure や k3s convergence failure の原因が追いにくい。

## 決定

通常 release 更新では、TFTP artifact と NFS rootfs を同じ release 名の [paired clean release bundle](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/release_bundle.yml) として作る。

stage は日付や世代識別子を持ち、[staging promote playbook](../../ansible/openwrt/playbooks/pxe-release-bundle-staging.yml) が host class に応じて paired release を導出する。

通常 promote では `tftp_release == rootfs_release` を invariant として扱う。canary / rollback のために host 個別 override を使う場合は、一時的な例外として明示する。

## 理由

release 名だけで boot artifact と rootfs の対応を追える。

boot failure 時に、TFTP 側と NFS rootfs 側の世代ずれを短時間で確認できる。

paired release は storage を少し多く使うが、家庭内検証環境では storage 節約よりも確認負荷の低さを優先する。

## 不採用案

### shared TFTP generation

現時点では不採用。

TFTP artifact を共有し、rootfs だけ host class ごとに分ける構成には価値がある。ただし `tftp_release != rootfs_release` が通常状態になり、manifest を読まないと正しい組み合わせを判断できない。

採用するには、[manifest-driven promote](../../ansible/openwrt/playbooks/tasks/pxe_release_bundle_build_and_manifest.yml) / rollback / [pruning](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/rootfs_prune.yml) と、それを検証する guard が必要である。

### rootfs だけを通常 release として進める

不採用。

kernel module、initramfs、firmware、rootfs の不整合が起きた場合に原因を追いにくい。

## 影響

Pi 世代や host class ごとに TFTP artifact が重複する場合がある。

pruning では paired release をまとめて扱う必要がある。

host 個別 canary は通常 promote と区別し、完了後は stage 由来の paired release へ戻す。

## 見直し条件

- TFTP artifact の重複が storage / build time 上の実問題になった場合。
- manifest-driven release management が整い、shared TFTP generation の方が確認負荷を下げられると判断できた場合。
