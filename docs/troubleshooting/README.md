---
status: current
audience: human-ai
scope: troubleshooting-index
last_reviewed: 2026-07-22
---

# Troubleshooting index

このdirectoryには、`bash scripts/pi-k3s-status`が検出できる公開可能な障害caseについて、背景、
read-only evidence、operator-gatedな対応方針を記録する。

## k3s status cases

- [k3s node NotReady triage](k3s-node-not-ready.md)
- [k3s agent registration authentication mismatch](k3s-agent-registration-auth-mismatch.md)

## 追加ルール

新しいcaseを追加する場合は、次を同じ変更で更新する。

1. このdirectoryのpublic-safeな説明文書
2. `.agents/skills/homecluster-convergence-monitor/references/k3s-status-remediation-catalog.json`
3. status collector、diagnoser、classifierのうち必要なdeterministic signal
4. unit testとfixture test

`pi-k3s-status`は修復を実行しない。出力された`remediation_url`をoperatorまたは上位agentが開き、
原因とgateを確認してから別workflowで対応する。
