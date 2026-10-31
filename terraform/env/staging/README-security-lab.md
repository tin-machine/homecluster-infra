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

## Bounded pipeline

境界とNFS権限の受入後、`security_lab_pipeline_enabled=true`と明示的な`security_lab_home_nets`を渡す。
初期UID/GIDは10001。NFSで同じnumeric ownerの0700 directory・0600 file、fsyncと別processの
lock排他を確認する。root_squash/mapall等で一致しなければ、検証したnumeric UIDを指定する。
既存PVC全体のchmod/chownは行わない。通常の一時領域は64Mi以下とし、大きい出力は専用PVCに置く。

coordinatorは1 replica/Recreate。NFSのglobal flockを全engineの実行中保持し、cluster UID・
Job UID・config SHAをowner recordへ保存する。古いownerを時刻だけで捨てない。
設定更新前は実行中Jobが無いことを確認する。再構築後に旧clusterのownerが残れば照合待ちになる。

`runtime.py intake '<closed manifest JSON>'`へ標準入力でPCAPを渡す。router producerのschemaと
pcap_sha256/size_bytes/packet_count等を照合し、PCAP→manifest→固定configに束ねたrequestの順に
fsync/公開する。durable receipt後にだけ管理側からrouterへACKする。
専用SSH鍵・NAS credentialをPodへ渡さない。部分転送・不一致・古いqueueを黙って上書きしない。
同じartifactの二重配送は再queueせず、設定更新による自動再解析も行わない。

各JobはSuricata→Zeekのinit containerとterminal containerから成る。入力はread-only、
出力はanalysis別subPath、non-root、capability全削除、RuntimeDefault、read-only root、tokenなし。
低優先度の専用PriorityClassはpreemptionPolicy Neverであり、既存workloadを退避させない
（[Kubernetes公式説明](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/)）。
request/limitはengineごと2Gi、limit 2 CPU、nice +10、Job deadline 600秒、engine 480秒、retryなし。
host MemAvailable、Node pressure、Job/Pod枠、NFS空き・各1Gi出力上限を監督し、異常時は自身の
Job UIDだけを停止する。OOM/欠落結果/Job Completeだけを成功へ補完しない。

固定image digest、設定hash、engine結果を保存する。local rulesの1000001は無害な検証用ICMP、
1000002は外部からのSSH SYN反復の検知候補であり、侵害確定とは表現しない。広範なIDS coverageを
保証するrulesetではない。更新は解析Jobの外でconfigテストと合成正例・負例を通す。

結果manifestとoutboxはPVCに残し、candidateを最大32件とsummaryをJSON stdoutへ出す。
元event時刻と解析時刻を分ける。Job成功・stdout提出・prod Loki到着は別状態。
`replay <analysis-id>`は保存済みevent IDを保持し、engineを再実行しない。
`delivery-ack <analysis-id>`は管理側がLoki到着を照合した後だけ使う。
短命Pod専用Collector、通知、保持cleanupは別の有効化条件として受け入れる。

検証: runtimeの合成fixture、`tests/security_lab_pipeline.tftest.hcl`と1.14互換性、
`python3 -B scripts/ci/check-security-lab-engines.py`による固定imageの実config/正例・負例。
Docker daemonが別filesystemなら、共有しているfixture directoryをTMPDIRに指定する。
CIのnative image検証だけでARM64/NFS実行を受け入れたとは扱わない。

## Dedicated log delivery

`security_lab_logs_enabled`は既定falseで、O4の認証付きprod配送を必要とする。
解析coordinatorの有効化にはこのflagも必要。専用overlayを最後に重ね、security-labのPodログだけを
beginningから読み、通常Podのstart_at=endは保持する。通常receiverから同namespaceを除外し、二重収集しない。
resource service.nameはpcap-analysis、event種別・engine・parser結果はmetadata、個々のIDはbodyに保つ。

file_storageのoffset/queueはk3s persistent data配下の専用hostPathへ保存する。初期化containerは
そのdirectoryだけを準備し、ext4/xfs・512MiB空き・専用marker・10001:0/0700を検証する。
不明な非empty directoryを採用せず、RAM overlayやPVC全体の所有権変更も行わない。
既存O4 volume/mountを保持し、他のログpipelineは変えない。

永続queueでも、満杯時の未受理batchが再起動で失われる場合がある。Job/提出完了を配送成功と扱わず、
管理側が保存済みoutboxのevent IDとLoki到着を照合してからdelivery-ackする。
未着分は保存済みevent IDで再送し、engineを再実行しない。

`test_security_lab_otel.py`は実Collectorの短命ログ・parser・record上限・offset再起動・rotation・
queue飽和と再送を検証する。2048件のfixtureでenqueue失敗counterを実測し、未着を同じevent IDで
再送して回復した。試験Docker volumeがext4/xfs以外なら、本番guardの拒否を確認してから、
initializerの権限試験に限りmountinfoを模擬する。実機のpersistent mount受入は別に行う。

## Router transfer and delivery receipts

Use `scripts/security_lab_transfer.py --help` on the operator host. It requires
explicit SSH targets, the expected staging cluster and namespace UIDs, and the
selected worker. The helper rechecks the coordinator Pod UID before each exec.
Keep the forced-reader key outside the repository with mode 0600. The receipt
parent must be mode 0700; the local receipt contains metadata only. PCAP bytes
pass through bounded memory and are written only to the private NAS input PVC.

`transfer --capture-id <id> --reader-key <path> --receipt <path>` verifies the
router manifest/hash/size, then waits for a durable NAS receipt. Add
`--ack-router` to remove the router artifact after that receipt, or use
`ack-router --receipt <path>` separately. An interrupted or mismatched transfer
fails closed; inspect retained evidence rather than overwriting partial input.
Repeating a completed transfer verifies existing input without requeueing engines.

`check-delivery --receipt <path> --loki-reader <user@host>
--loki-cluster-uid <uid>` queries native Loki through an attested SSH connection
and compares every stored outbox event ID before saving the delivery receipt.
Query errors or missing records stay pending. `--replay-missing` requests a single
bounded retry of missing saved records through the coordinator main log stream;
engine execution is not repeated. A standalone `replay` also queues a durable
request: exec stdout itself is not a CRI log stream. Delivery may contain duplicate
records with the same event ID; it does not provide exactly-once transport.

`security_lab_nfs_version` defaults to 3. The actual mount version and remote
file locking are required before analysis; changing a PV or StorageClass option
alone does not validate an existing mount. Use the version required by the chosen
NAS and never disable locking to bypass a failed probe.
