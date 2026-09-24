---
status: current
audience: human-ai
scope: k3s-agent-registration-auth-mismatch
last_reviewed: 2026-07-22
---

# k3s agent registration authentication mismatch

## この文書を使う条件

`bash scripts/pi-k3s-status`が次のいずれかを返した場合に使用する。

```text
ai_case_id=k3s_agent_registration_auth_mismatch
ai_rule_candidates=...k3s_node_password_mismatch...
ai_rule_candidates=...k3s_agent_token_mismatch...
issues=...node_identity_signal...
remediation_id=k3s-agent-registration-auth-mismatch
```

代表的な実行時症状は、agent側の`not authorized`、control-plane側のnode session不成立、
Kubernetes nodeの`NotReady`または`NodeStatusUnknown`である。

## 背景

k3s agentの参加には、少なくとも次の状態が整合している必要がある。

- agentが接続するserver endpoint
- agentが使用するcluster token
- agent側に保存されたnode passwordと証明書類
- control-plane datastore内のnode password Secretとcluster identity

PXE root overlayやcontrol-plane datastoreが再生成される構成では、server側が新しいcluster identityへ
移行しても、local block backed storage上のagent identityが残ることがある。このときserverとagentが
異なる世代の状態を参照し、認証に失敗する。

この設計上の境界は
[ADR 0007](../architecture-decision-record/0007-k3s-agent-identity-cache-boundary.md)に記録している。

## 原因の分類

### Node password mismatch

次の組み合わせは、agent側のnode passwordとcontrol-plane側Secretのhashが一致しないことを示す。

```text
node_password_match=false
node_password_reason=hash_mismatch
```

主な背景:

- control-plane datastoreが再生成された
- agent identityだけが永続storageに残った
- node password Secretとagent側passwordが別のjoin世代に由来する

### Agent token mismatch

次の組み合わせは、agentの実効token sourceとcontrol-planeのagent tokenが一致しないことを示す。

```text
token_match=false
token_match_reason=credential_mismatch
```

主な背景:

- inventory、systemd environment、config、token-fileの更新順がずれた
- agentが古いtoken-fileを参照している
- server側tokenが更新または再生成された

### Wrong server endpoint

agentが意図したcontrol-plane以外へ接続している場合も、同じ`not authorized`に見えることがある。

```text
server_url=<unexpected endpoint>
```

endpoint、token、identityを同時に変更せず、一つずつsource of truthと照合する。

## 対応方針

修復は自動実行しない。`pi-k3s-status`の
`ai_operator_gate_required=true`と`ai_automatic_repair_allowed=false`を維持し、operatorが対象nodeと
修復範囲を確認してから行う。

### 1. Read-only evidenceを確定する

まず次を確認する。

```text
not_ready_nodes
server_url
owner_unit
process_role
token_source_kind
token_source_origin
token_file_path
node_password_match
token_match
```

node passwordとtokenの両方が不一致なら、片方だけを直して完了扱いにしない。

### 2. Server側をsource of truthとしてtoken sourceを揃える

agentのsystemd unit、environment file、config、token-fileのうち、実行中processが実際に参照している
sourceを特定する。そのsourceをAnsible管理値へ戻し、別の古い定義が優先されていないことを確認する。

raw tokenをterminal、log、Git、LLM promptへ出力しない。比較にはsecret-freeな一致判定を使う。

### 3. Agent identityだけをresetする

server側cluster identityが変わった場合、古いnode password、agent certificate、kubeconfigなどの
identity fileをresetして再joinさせる。

containerd image cacheはidentityではないため、同時に削除しない。identity resetとcache cleanupを
分けることで、原因の切り分けと再収束時間を維持する。

公開側の実装方針は次を参照する。

- [k3s node password sync helper](../../ansible/arm64/roles/k3s_local_storage/templates/k3s-node-password-sync.sh.j2)
- [ADR 0007](../architecture-decision-record/0007-k3s-agent-identity-cache-boundary.md)

### 4. 再確認する

修復後に`bash scripts/pi-k3s-status`を再実行し、少なくとも次を確認する。

```text
nodes_ready=<expected>/<expected>
not_ready_nodes=none
issues=none
node_password_match=true
node_password_reason=verified
token_match=true
token_match_reason=verified
```

詳細診断を接続していないpublic core単体ではcredential match fieldが出ない場合がある。その場合でも
nodeが`Ready`へ戻り、`node_identity_signal`が消えたことを確認する。

## やってはいけないこと

- 原因確認前にagent data-dir全体を削除する
- identity resetとcontainerd cache cleanupを同時に行う
- raw tokenまたはnode passwordを表示、転送、記録する
- server endpoint、token、identityを一度に変更する
- `NotReady`が消える前に修復完了と判断する

## 再発防止

- server CA hashまたはcluster identityの変化をboot時に検出する
- identity stateとcontainerd cacheを別directory、別lifecycleで管理する
- token-fileとsystemd/configのsourceをAnsibleで一意にする
- `pi-k3s-status`へ新しいsignatureとremediation mappingを追加する
- 新しいcaseはpublic-safeな原因説明とoperator-gatedな対応方針をこのdirectoryへ追加する
