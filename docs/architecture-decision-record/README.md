---
status: current
audience: human-ai
scope: architecture-decision-record-index
last_reviewed: 2026-05-31
---

# 設計判断記録 (ADR)

この directory は、公開 repository に残してよい設計判断だけを置く。

operation 手順、troubleshooting raw log、実 host / IP / path / serial / token、private repository 名、長い検証経緯はここに置かない。必要な詳細は private runbook 側で維持する。

## ADR 一覧

| ADR | 状態 | 判断 |
| --- | --- | --- |
| [ADR 0001](0001-public-private-repository-boundary.md) | accepted | 公開 repository と private 実運用 repository を分ける |
| [ADR 0002](0002-docs-system-to-adr-and-runbook.md) | accepted | `docs/system/` を公開側にそのまま残さず ADR と runbook に分解する |
| [ADR 0003](0003-pxe-root-overlay-disposable.md) | accepted | PXE root overlay は disposable にする |
| [ADR 0004](0004-raspberry-pi-tftp-host-directory.md) | accepted | Raspberry Pi TFTP は board hash 実 directory を優先する |
| [ADR 0005](0005-pxe-release-bundle-paired.md) | accepted | PXE release は TFTP artifact と NFS rootfs を paired bundle として扱う |
| [ADR 0006](0006-k3s-runtime-storage-local-block.md) | accepted | k3s runtime storage は root overlay / NFS ではなく local block backed filesystem を使う |
| [ADR 0007](0007-k3s-agent-identity-cache-boundary.md) | accepted | k3s agent identity と containerd cache を分けて扱う |
| [ADR 0008](0008-terraform-crd-bootstrap-state.md) | accepted | Terraform CRD bootstrap state を依存 addon state から分ける |
| [ADR 0009](0009-public-ci-local-apply-boundary.md) | accepted | 公開 CI と local apply を直接接続しない |
| [ADR 0010](0010-inventory-boundary.md) | accepted | 実 inventory は repository 外に置き、公開側は example と path contract だけを持つ |
| [ADR 0011](0011-pxe-ssh-host-identity.md) | accepted | PXE SSH host identity を rootfs release から分離する |
| [ADR 0012](0012-executable-public-source.md) | accepted | 公開 repository を実行可能な正本として維持する |

## 書式

各 ADR は次の粒度に揃える。

- `状況`: 判断が必要になった背景。
- `決定`: 採用する構成。
- `理由`: 採用理由。
- `不採用案`: 捨てた案と理由。
- `影響`: 運用上の trade-off。
- `見直し条件`: 将来再検討する条件。
