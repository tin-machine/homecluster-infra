---
status: current
audience: human-ai
scope: k3s-node-not-ready
last_reviewed: 2026-07-22
---

# k3s node NotReady triage

## この文書を使う条件

`bash scripts/pi-k3s-status`が次を返した場合に使用する。

```text
issues=...nodes_not_ready...
issues=...missing_expected_nodes...
remediation_id=k3s-node-not-ready
```

この文書は原因未確定時の入口である。より具体的なcase IDとremediation URLが出ている場合は、
そちらを優先する。

## 背景

Kubernetes nodeの`NotReady`は原因ではなく、kubeletまたはk3s agentがcontrol-planeへ期待どおり
statusを送れない結果である。主な分類は次のとおり。

- hostまたはnetworkへ到達できない
- k3s serviceが停止または起動失敗している
- server endpoint、token、node identityが整合していない
- filesystem、mount、memory、disk、PIDに問題がある
- boot後のAnsibleまたはpackage収束が未完了である

## 確認順序

### 1. Status出力を保存する

次を確認する。

```text
nodes_ready
not_ready_nodes
node_pressure
issues
diagnostic_triggers
diagnostic_findings
remediation_url
```

### 2. 到達性とservice ownershipを確認する

nodeへのSSH、k3s data-dir mount、実行中process、所有systemd unitを確認する。

SSH host key問題はcluster障害と分けて扱う。`ssh_host_key_problem`が出た場合は、known_hostsと
host identityを先に確認する。

### 3. Pressureを確認する

`node_pressure`が0でない場合は、DiskPressure、MemoryPressure、PIDPressureを区別する。

pressureが原因なら、認証identityをresetしない。storage、memory、process数の原因を先に解消する。

### 4. 認証signalを確認する

`not authorized`、node password、token、authorizationに関するsignalがある場合は、
[k3s agent registration authentication mismatch](k3s-agent-registration-auth-mismatch.md)へ進む。

### 5. 再確認する

対応後に`bash scripts/pi-k3s-status`を再実行し、対象nodeが`Ready`へ戻り、関連issueが消えたことを
確認する。Podとnode-exporterの収束も別に確認する。

## 修復の境界

`pi-k3s-status`はread-onlyであり、自動修復しない。

- service restart
- identity reset
- token source変更
- storage cleanup
- Kubernetes object削除

これらは原因と対象を確定し、operator gateを通した別workflowで行う。

## 新しいcaseを追加する場合

既存文書で説明できない再現性のあるsignatureを確認した場合は、次を同じ変更で追加する。

1. public-safeなtroubleshooting文書
2. remediation catalogのcase、issue、trigger mapping
3. fixtureまたはunit test
4. `pi-k3s-status`が返すboundedなevidence

raw log、実host名、private address、credentialは公開文書へ追加しない。
