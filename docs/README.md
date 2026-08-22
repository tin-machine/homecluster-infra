---
status: current
audience: human-ai
scope: docs-index
last_reviewed: 2026-07-22
---

# ドキュメント索引

このdirectoryは、`homecluster-infra`の公開可能な設計文書を置く。

raw operation runbook、troubleshooting log、実host値、private repository URL、secret、kubeconfig、
Terraform state、vendor payloadのメモは含めない。

## 最初に読むもの

- [ルート README](../README.md): 対象範囲、inventory contract、OpenWrt live input、Terraform layout、secret境界、CI境界。
- [AI context](ai-context.md): AI支援作業向けの圧縮済み公開可能コンテキスト。
- [Memory](memory.md): 公開可能な短い履歴メモ。
- [Troubleshooting index](troubleshooting/README.md):
  `pi-k3s-status`が検出する既知caseの背景、evidence、operator-gatedな対応方針。
- [k3s observability validation checklist](k3s-observability-validation-checklist.md):
  Terraform管理のstaging observability stackを確認するための公開可能なoperator checklist。
- [Full execution validation](full-execution-validation.md):
  網羅的なoffline validation、site-local admission、controlled live convergenceを分ける文書。
- [Publication readiness gate](publication-readiness-gate.md):
  repository visibility変更前のmetadata、GitHub settings、go/no-go確認。
- [外部入力](site-input-contract.md):
  Terraform root固有入力、Helm chart固有入力、Ansible live inputの境界。
- [OpenWrt live apply plan](openwrt-live-apply-plan.md):
  OpenWrt tagless applyを避け、network、DHCP、dnsmasq query log、FRR、syslog、firewall、banIPを
  分けるcontrolled apply plan。
- [設計判断記録](architecture-decision-record/README.md): 公開可能な設計判断。
- [Inventory storage options](inventory-storage-options.md): 外部inventory保存方式の比較。
- [Example inventory](../examples/inventory.yml): documentation addressを使った公開用inventory形状。
- [License](../LICENSE)と[NOTICE](../NOTICE): repository licenseとthird-party dashboard notice。

## 設計判断記録

- [ADR 0001](architecture-decision-record/0001-public-private-repository-boundary.md):
  公開repositoryとprivate repositoryの境界。
- [ADR 0002](architecture-decision-record/0002-docs-system-to-adr-and-runbook.md):
  system docsをADRとprivate runbookに分解する。
- [ADR 0003](architecture-decision-record/0003-pxe-root-overlay-disposable.md):
  PXE root overlayをdisposableにする。
- [ADR 0004](architecture-decision-record/0004-raspberry-pi-tftp-host-directory.md):
  Raspberry Pi TFTP board hash directory。
- [ADR 0005](architecture-decision-record/0005-pxe-release-bundle-paired.md):
  TFTP artifactとNFS rootfs release bundleをpairedに扱う。
- [ADR 0006](architecture-decision-record/0006-k3s-runtime-storage-local-block.md):
  k3s runtime storageにlocal block backed filesystemを使う。
- [ADR 0007](architecture-decision-record/0007-k3s-agent-identity-cache-boundary.md):
  k3s agent identityとcontainerd cacheの境界。
- [ADR 0008](architecture-decision-record/0008-terraform-crd-bootstrap-state.md):
  Terraform CRD bootstrap stateの分離。
- [ADR 0009](architecture-decision-record/0009-public-ci-local-apply-boundary.md):
  公開CIとlocal applyの境界。
- [ADR 0010](architecture-decision-record/0010-inventory-boundary.md): 外部inventory境界。
- [ADR 0011](architecture-decision-record/0011-pxe-ssh-host-identity.md): 永続するPXE SSH host identity。
- [ADR 0012](architecture-decision-record/0012-executable-public-source.md): 実行可能な公開側の正本。

## 公開ドキュメントのルール

- address例には`192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24`などのdocumentation rangeを使う。
- 実inventory、host名、token、password、serial、private path、raw log、generated stateはこのrepositoryに入れない。
- 公開する設計判断はADRへ寄せる。再現可能なgeneric troubleshootingは`docs/troubleshooting/`へ置く。
- raw investigation contextはprivate runbookに置く。
