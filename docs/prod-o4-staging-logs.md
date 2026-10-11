# O4 — staging logsをproduction Lokiへ保存するsource-only基盤

## 現在の実装範囲

`scripts/prod_o4_loki_render.py` は**新規のlog backend 6resourceのみ**を決定論的JSON Listにrenderする。productionの既存Prometheus/GrafanaのConfigMap、Deployment、PVC/PV、NetworkPolicy、alert rulesを変更しない。以下は**source-onlyであり、実機導入・転送成功・保存成功を意味しない**。

| object | role |
| --- | --- |
| ConfigMap/`prod-loki-config` | Loki monolithicの固定schema/limits |
| PVC/`prod-loki-data` | 専用4Gi local-path / RWO、既存Prometheus PVCとは分離 |
| Deployment/`prod-loki` | 1 replica、Recreate、non-root、専用PVC |
| Service/`prod-loki` | ClusterIP port 3100のみ |
| NetworkPolicy/`prod-loki-restricted` | ingressはGrafana Podのみ、egressを拒否 |
| NetworkPolicy/`prod-grafana-loki-egress` | GrafanaからLokiへのTCP/3100だけを追加許可 |

Loki imageは `grafana/loki:3.7.8` に固定。Monolithic構成は公式 `v3.7.8/cmd/loki/loki-local-config.yaml` と同じ `common.instance_addr: 127.0.0.1` / `ring.kvstore: inmemory` / `replication_factor: 1` に固定し、同一processのring通信をPod IPではなくloopbackへ寄せる。Loki PodのNetworkPolicy egress全拒否を安易に緩和しない。KubernetesでのNetworkPolicy enforcement/loopback動作は採用CNIに依存するため、source testと隔離container testだけでは実機受入れ完了にならない。production ARM64 manifest digest・実Pod起動・PVC permissionは**実機導入前に別途検証**する。snapshotが古くなる場合はsource revisionとtestsをセットで更新する。

参照: [Grafana Loki v3.7.8単一バイナリ設定](https://github.com/grafana/loki/blob/v3.7.8/cmd/loki/loki-local-config.yaml)、[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)。

初期のLokiは `auth_enabled: false` の**single-tenant内部Service限定**であり、LANやstagingからpush可能なendpointを持たない。Loki APIに認証機能を期待してはいけない。認証/TLS付きpush gateway、NetworkPolicy許可、site-local credential、およびstaging側送信を**別レビューのPRとoperator承認**なしに追加してはならない。

## Storage / retention

- Monolithic Loki / filesystem / TSDB schema v13 / 24h index / ring `inmemory` / replication factor 1。
- `limits_config.retention_period=72h`、`compactor.retention_enabled=true`。retentionはcompactorが処理するので、設定値だけでは直ちに物理容量が減るとは限らない。
- log write budget: 1MiB/s相当のrate設定と2MiB burstを初期上限として固定。CPU/memory requests/limits、single-writer PVC、`readOnlyRootFilesystem`/tmp emptyDirを設定。
- PVC 4GiはKubernetesの**要求容量でありhard quotaではない**。node free-space、inode、Loki compactorとdisk growthを別途監視し、実測log流量/日と保持期間によって容量を再調整する。
- local-path volumeや下位storageの障害はprod Lokiの喪失原因となる。これをstagingとは別のfailure domainとして扱えるか、物理storage topologyを別途確認する。

## 次のsource-only PR

1. production側のTLS認証付きログ専用gateway、host/network制限、read/writeの分離、必要なowner-only credential contract。
2. staging Collectorの既存`filelog/pods`→staging Lokiの経路を維持しつつ、新しいOTLP HTTP exporterでprod Loki native OTLP `/otlp/v1/logs`へ配送する。Collector exporter設定は`https://<private-gateway>/otlp`というbase endpointとし、`/v1/logs`はOTLP HTTP exporterに任せる。
3. 既存Collectorの追加exporterが既存staging backendへのdeliveryやmetrics/tracesを阻害しないことをbounded queue/retry/timeoutと負例fixtureで固定。もし分離不能ならログ専用Alloy agent等を検討。
4. journaldの収集は別途実装が必要。既存`filelog/pods`をもってsystemd journalが転送できていると判断しない。journalctl availability、read permissions、boot ID/cursorとreboot resilienceをread-only discoveryしてからsourceを作る。
5. Loki受入れ後にGrafana Loki datasourceのadditive source管理を追加し、既存Prometheus datasource・8panelを変更しないことを検証。

## CI / source-only検証

```bash
python3 scripts/prod_o4_loki_render.py --output /tmp/prod-o4-loki-manifest.json
python3 -m unittest discover -s scripts -p 'test_prod_o4_loki_render.py' -v
```

- rendererはstdoutまたはowner-only/exclusive-createの`--output`のみで、既存ファイルを上書きしない。
- fixtureは生成6resource、namespace、single replica/Recreate、PVC独立、Loki config、retention、network ingress denyとGrafana限定例外、checksum、および再生成のdeterminismとsymlink/overwrite拒否を確認する。
- 追加のCI・PRにkubeconfig、host名、IP、credential、raw log、site inputは含めない。
- source CIの成功はKubernetes API schema admissionやLoki imageのruntime configuration loadの代用にならない。

## 実機acceptanceへの移行境界

prod backendのruntime createは、mainにmergeしたsource/full SHAを固定し、owner-only site state、production cluster identity、既存Prometheus/Grafana/TSDB/PVC resource identity、PVC独立、NetworkPolicy reachabilityを確認できる**専用のoperator-approved fixed operation**を実装した後に限る。bootstrap前に既存15resourceのUID/baseline、bootstrap後に同一UID/TSDB historyを確認する。既存production resourceのupdateを伴う場合は変更対象と承認を別定義する。

O4 acceptanceはLoki Pod Readyだけで完了しない。stagingからのcontainer logとjournalをprodへ転送し、staging APIが参照できない想定でもprodからLogQLで時刻・node・workload別に検索できること、delivery失敗/再送/重複/retentionの監視が成立することを別途確認する。実stagingの障害注入は不要。

参考:
- https://grafana.com/docs/loki/latest/setup/install/helm/install-monolithic/
- https://grafana.com/docs/loki/latest/operations/storage/retention/
- https://grafana.com/docs/loki/latest/send-data/otel/
- https://grafana.com/docs/loki/latest/reference/loki-http-api/
