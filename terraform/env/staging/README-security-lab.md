# security-lab offline analysis boundary

`security_lab_offline_enabled`は既定false。有効にすると既存namespace/PVCを使い、解析workerの
専用label、restricted Pod Security、launcherの権限、限定したAPI egressを構成する。

```hcl
security_lab_offline_enabled = true
security_lab_worker_node     = "worker-01"
```

workerは既存のReadyなLinux/ARM64 nodeから選ぶ。control-plane、スケジューリング停止、
NoSchedule/NoExecute taint、pressure、条件欠落・重複はplan時に拒否する。
実行前には実MemAvailableと既存requests、image領域、NFS権限も確認する。
このlabelは配置許可であり、空き容量の保証ではない。

`kubernetes_labels.security_lab_worker`が`security-lab.home.arpa/analysis=true`だけを管理し、
他のlabelは保持する。K3sの`--node-label`は登録時の設定なので、登録済みnodeの更新と起動後の
再収束をTerraformで管理する（[K3s公式説明](https://docs.k3s.io/cli/agent#node-labels-and-taints-for-agents)）。

有効時は既存のDNS/Loki allow Policyから広いegress例外を取り除く。
coordinator labelを持つPodだけ、Kubernetes Service IPのTCP443と、観測したcontrol-plane
InternalIPのTCP6443へ接続できる。IPv4は/32、IPv6は/128を使用する。
coordinator以外のoffline解析Podはdefault-denyの対象になる。実拒否は実機の正例・負例で確認する。
PVCのNFS mountはnode側の処理として扱う。

launcher用ServiceAccountはtoken自動mountを既定無効とし、namespace内のJob操作とPod/log読取、
指定worker一台のNode読取だけを許可する。coordinatorは必要なtokenだけを明示して使い、
解析Podはtokenをmountしない。Pod/Job renderer側でもnon-root、capability削除、read-only root、
RuntimeDefault seccomp、固定worker、入力read-only、resource/deadline制約を検証する。

Job作成・結果保存・NFS上の永続queue/lockはlauncherが担当する。時刻だけで古いlockを破棄せず、
前のJob identityとterminal resultを照合する。Job成功とprodログへの到着も別に記録する。

source validationはTerraform validateと`tests/security_lab_offline.tftest.hcl`を使う。
既定無効の互換性、worker選択の不正/欠落、restricted/RBAC/egress、IPv6のhost単位制約を検証する。
