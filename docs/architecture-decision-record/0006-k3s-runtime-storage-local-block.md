---
status: accepted
audience: human-ai
scope: k3s-runtime-storage
last_reviewed: 2026-05-31
---

# ADR 0006: k3s runtime storage は local block backed filesystem を使う

## 状況

PXE node の root filesystem は disposable な tmpfs overlay である。root overlay は OS の一時差分には向くが、container image unpack、containerd snapshot、k3s state、database などの runtime write path には容量と local filesystem semantics が不足しやすい。

NFS は shared data や backup には便利だが、container runtime や database のような local disk semantics を要求する path には向かない。

## 決定

[k3s / containerd data-dir](../../ansible/arm64/roles/k3s_local_storage/tasks/main.yml) は root overlay や NFS へ置かず、node local block backed filesystem を使う。

[OpenWrt](../../ansible/openwrt/site.yml) は DHCP / TFTP / NFS rootfs / metadata の基盤に留め、k3s runtime write path から外す。

NFS は shared PVC、成果物、backup、軽い profile 置き場として使う。local disk semantics が必要な path には使わない。

## 理由

container image unpack と snapshotter は容量と filesystem semantics に敏感である。

root overlay 上に data-dir を置くと、image pull だけで DiskPressure や no space に寄りやすい。

NFS 上に data-dir を置くと、lock、rename、fsync、snapshotter の前提が崩れやすい。

node local block backed filesystem は構成が単純で、障害時も node 単位で切り分けられる。

## 不採用案

### root overlay 上に k3s data-dir を置く

不採用。

reboot で state が消え、容量も限られる。軽い smoke test 以外の runtime path には向かない。

### NFS PVC / NFS mount を k3s data-dir に使う

不採用。

shared data には適していても、container runtime の data root としては local disk semantics の不足が問題になりやすい。

### router 側 storage を k3s runtime write path にする

現時点では不採用。

router 障害が boot、network、storage、runtime に同時波及する。家庭内検証環境では、router は boot infrastructure に留める方が単純である。

## 影響

node ごとに local storage の mount、format、lifecycle を管理する必要がある。

local storage を disposable とするか永続 state とするかを、node / workload ごとに明示する必要がある。

[NFS provisioner を使う workload と local-path / hostPath を使う workload](../../terraform/modules/observability_stack/main.tf) は、`storageClassName` や node placement で明確に分ける。

## 見直し条件

- central block storage が安定し、router / boot infrastructure と failure domain を分けられる場合。
- local block device を各 node に置く運用の方が複雑になった場合。
- container runtime が remote filesystem を明示的に安全サポートする構成へ変わった場合。
- openwrt上でiSCSIが安定して提供できるようになった場合
