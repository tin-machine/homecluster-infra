---
status: current
audience: human-ai
scope: docs-index
last_reviewed: 2026-06-08
---

# ドキュメント索引

この directory は、`homecluster-infra` の公開可能な設計文書を置く。

raw operation runbook、troubleshooting log、実 host 値、private repository URL、secret、kubeconfig、Terraform state、vendor payload のメモは含めない。

## 最初に読むもの

- [ルート README](../README.md): 対象範囲、inventory contract、OpenWrt live input、Terraform layout、secret 境界、CI 境界。
- [AI context](ai-context.md): AI 支援作業向けの圧縮済み公開可能コンテキスト。
- [Memory](memory.md): 公開可能な短い履歴メモ。
- [k3s observability validation checklist](k3s-observability-validation-checklist.md):
  Terraform 管理の staging observability stack を確認するための公開可能な operator checklist。
- [Full execution validation](full-execution-validation.md):
  網羅的な offline validation、site-local admission、controlled live convergence を分ける文書。
- [Publication readiness gate](publication-readiness-gate.md):
  repository visibility 変更前の metadata、GitHub settings、go/no-go 確認。
- [外部入力](site-input-contract.md):
  Terraform root 固有入力、Helm chart 固有入力、Ansible live input の境界。
- [OpenWrt live apply plan](openwrt-live-apply-plan.md):
  OpenWrt tagless apply を避け、network / DHCP / dnsmasq クエリログ / FRR / syslog / firewall / banIP を分ける controlled apply plan。
- [設計判断記録 (ADR)](architecture-decision-record/README.md):
  公開可能な設計判断。
- [Inventory storage options](inventory-storage-options.md):
  外部 inventory 保存方式の比較。
- [Example inventory](../examples/inventory.yml):
  documentation address を使った公開用 inventory 形状。
- [License](../LICENSE) と [NOTICE](../NOTICE):
  repository license と third-party dashboard notice。

## 設計判断記録 (ADR)

- [ADR 0001](architecture-decision-record/0001-public-private-repository-boundary.md):
  公開 repository と private repository の境界。
- [ADR 0002](architecture-decision-record/0002-docs-system-to-adr-and-runbook.md):
  system docs を ADR と private runbook に分解する。
- [ADR 0003](architecture-decision-record/0003-pxe-root-overlay-disposable.md):
  PXE root overlay を disposable にする。
- [ADR 0004](architecture-decision-record/0004-raspberry-pi-tftp-host-directory.md):
  Raspberry Pi TFTP board hash directory。
- [ADR 0005](architecture-decision-record/0005-pxe-release-bundle-paired.md):
  TFTP artifact と NFS rootfs release bundle を paired に扱う。
- [ADR 0006](architecture-decision-record/0006-k3s-runtime-storage-local-block.md):
  k3s runtime storage に local block backed filesystem を使う。
- [ADR 0007](architecture-decision-record/0007-k3s-agent-identity-cache-boundary.md):
  k3s agent identity と containerd cache の境界。
- [ADR 0008](architecture-decision-record/0008-terraform-crd-bootstrap-state.md):
  Terraform CRD bootstrap state の分離。
- [ADR 0009](architecture-decision-record/0009-public-ci-local-apply-boundary.md):
  公開 CI と local apply の境界。
- [ADR 0010](architecture-decision-record/0010-inventory-boundary.md):
  外部 inventory 境界。
- [ADR 0011](architecture-decision-record/0011-pxe-ssh-host-identity.md):
  永続する PXE SSH host identity。
- [ADR 0012](architecture-decision-record/0012-executable-public-source.md):
  実行可能な公開側の正本。

## 公開ドキュメントのルール

- address 例には `192.0.2.0/24`、`198.51.100.0/24`、`203.0.113.0/24` などの documentation range を使う。
- 実 inventory、host 名、token、password、serial、private path、raw log、generated state はこの repository に入れない。
- 公開する設計判断は ADR に寄せる。raw investigation context は private runbook に置く。
