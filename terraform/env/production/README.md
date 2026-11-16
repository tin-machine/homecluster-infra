---
status: current
audience: human-ai
scope: terraform-production-public-placeholder
last_reviewed: 2026-11-16
---

# Terraform production

ARCの試験導入用に、次の独立rootを用意する。

- `arc-controller`: ARC operator / CRDと専用namespace。
- `arc-runners`: runner namespace、ServiceAccount、NetworkPolicy、資源上限、任意のscale set。

controllerを先に適用する。両rootのstate、tfvars、kubeconfigをrepository外で分けて保存する。
runnerは既定で無効。境界resourceを作成し、外部から認証Secretを注入してから有効にする。
Terraformへ渡すのは登録先URLとSecret名だけ。token実値をvariableやHelm valuesへ渡さない。
stagingのcommon-crds / staging stateと共用しない。

試験構成はidle runner 0、最大1、CPU 2 / memory 2Gi / ephemeral-storage 2Giの上限を持つ。
`emptyDir.sizeLimit`は1536Mi。PVCは使用しない。
一時ディスク上限はkubeletの測定とevictionに依存し、即時のfilesystem書込quotaではない。
nodeの既存空き容量とimage増分を別に確認する。
Podはnon-root、権限昇格なし、ServiceAccount tokenなしとし、公開HTTPSとDNSだけを許可する。
NetworkPolicyとnamespaceを、node kernelや本番workloadの完全な隔離として扱わない。

その他のproduction環境を一括構築するrootはまだ用意していない。
