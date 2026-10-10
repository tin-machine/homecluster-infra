# NanoCluster prod O3 — outside-in alert evaluation source-only

## 確定した現在地

O2 Step C/D: prod Prometheusはstaging4ノードのnode-exporterを直接scrapeし、
API/TCP Blackbox probeも正常。Grafanaは追加5resourceで稼働し、
dashboard8パネル、再provisioning、Prometheus TSDB/PVC維持を実機acceptance済み。

O3では**通知の前に正確な判定ルールを定義・単体テスト**する。
`scripts/prod_alert_rules_render.py`は
`observability-prod/ConfigMap/prod-staging-alert-rules` **1つだけ**を生成する。
既存O1/O2の10resource、Grafana5resource、prod環境へは一切変更を加えない。
source-only PRのmergeをlive alerting導入完了と見なさない。

## なぜPrometheusで評価するか

Prometheusはprod（stagingと別failure domain）にあり、
blackbox Probeとnode exporterの時系列を実際に保存する。
GrafanaのDBは初回構成では`emptyDir`のため、
**Grafana内部の状態を唯一のアラート権威としない**。
評価はPrometheus alerting rules、通知は独立したAlertmanagerを
後続で組み合わせる方針とする。通知先はstaging cluster内に置かない。

## 判定ルールと不在検知

`alerts.json`はJSONで書いたPrometheus互換のYAMLルール。
8条件、groupの評価間隔30秒、`for`は2〜5分。

| alert | 条件 | hold |
| --- | --- | --- |
| StagingAPIProbeFailed | `probe_success{job="staging-api"} == 0` | 2m |
| StagingAPIBlackboxScrapeFailed | `up{job="staging-api"} == 0` | 2m |
| StagingAPIProbeTargetsMissing | `(count(up{job="staging-api"}) or vector(0)) < 1` | 5m |
| StagingTCPProbeFailed | `probe_success{job="staging-tcp"} == 0` | 3m |
| StagingTCPBlackboxScrapeFailed | `up{job="staging-tcp"} == 0` | 3m |
| StagingTCPProbeTargetsMissing | `(count(up{job="staging-tcp"}) or vector(0)) < 3` | 5m |
| StagingNodeExporterScrapeFailed | `up{job="staging-node-exporter"} == 0` | 3m |
| StagingNodeExporterTargetsMissing | `(count(up{job="staging-node-exporter"}) or vector(0)) < 4` | 5m |

重要な違い:

- `probe_success=0`は検査対象API/TCPの応答異常。
- `up{job="staging-api"}=0`はPrometheus→Blackbox scrape障害。
- `up{job="staging-node-exporter"}=0`はPrometheus→対象node scrape障害。
- `up=0`だけでは**設定からtargetが消滅した場合**に発火しない。
  count+vector(0)でjob全体が消えた場合も検知する。
- staging TCP probeはO1実機acceptanceで確認した**3 target**を期待値とし、
  1件または全件が構成から消えた場合も`StagingTCPProbeTargetsMissing`で検出する。
  意図してTCP target数を変更する際はprivate site input、
  `EXPECTED_STAGING_TCP_TARGETS`、synthetic fixtureを同時reviewする。
- 4node countは**現在のO2の固定4 target source contract**。
  台数変更の際はルール、site、dashboard、testsを同時reviewする。
  API 0件/TCP 0件/node-exporter 0件の欠損についてもpromtool fixtureで確認する。
- `count`による検証はtargetの**数**のみ。期待targetの1件が別の1件へ置換され、
  数が変わらない場合には検出できない。必要なら期待label集合との比較を後続検討する。
- `for`の2〜5分は仮置き。stagingリブート時の短い収束と
  通知遅延のトレードオフがある。live acceptanceで調整する。
- Prometheus停止時は自身のrules評価も止まるため、
  **prod外部heartbeat（O5）は別途必要**。

## source-only実行

```bash
python3 scripts/prod_alert_rules_render.py --output /tmp/prod-staging-alerts.json
python3 -m unittest discover -s scripts -p 'test_prod_alert_rules_render.py' -v
```

出力はConfigMap1件のみ、秘密情報や通知先を含まない。
`--output`は既存file上書きを拒否する。

Prometheusの本番反映には**別のoperator承認付き固定operation**が必要。
最低限、既存prod Prometheus configへ
`rule_files: ["/etc/prometheus/rules/alerts.json"]`を追加し、
ConfigMapをread-only volumeとしてPodにmountする必要がある。
既存の`checksum/config`、Prometheus`Recreate`、PVC/PV、
旧block/WAL、Grafana/Blackbox serviceと15resource UIDを保存・検証する。
既存`prod-observability.update-metrics`はO1→O2更新専用であり、**流用しない**。

ルールの構文・発火・解消は既存hosted `static-check`から実行する
`test_prod_alert_rules_render.py`で検証済み。構造テストに加えて、
**productionと同じPrometheus `v3.14.0` の`promtool check rules` / `promtool test rules`**
をpinned container上で実行し、14件のsynthetic時系列ケースを評価する。
対象には全正常、API/TCP probe失敗とBlackbox scrape失敗の分離、
TCP 3件中1件/全件のtarget消失、node-exporter 1件失敗・一部/全target消失、
stalenessと`for`の境界、短期障害の回復、pending→firing→resolvedを含める。
CIでは`--network=none`、read-only bind mount、非root、capability削除を使用し、
Dockerが使用できなければテストをskipせずfailとする。

**CIの合格はsynthetic seriesでのルール意味検証であり、実機へルールがロードされた証拠ではない。**
prod反映の前にはoperatorが最新main・manifest SHA・private site identityを照合し、
operator承認付き固定operationで実機preflightを実施する。
反映後はPrometheus `/api/v1/rules`で8ルールと状態を確認し、
PVC/PV・既存TSDB・O1/O2/GrafanaリソースUIDを確認する。

## 通知側（後続O3 Step B）

通知にはprod側のAlertmanagerを選ぶ。受け口はprod Prometheusから
同namespaceの限定TCP/9093で到達させる。Alertmanagerは
`group_by: [alertname]`、`group_wait: 30s`、`group_interval: 5m`、
`repeat_interval: 4h`、`send_resolved: true`を初期案とする。
通知先はowner-only Secretのファイルから読み、**public rendererには
webhook URL・Slack bot token・channel IDを含めない**。
現在staging側に存在するSlack設定をそのままコピーしたり、
staging Alertingに依存する構成にはしない。

運用者との相談事項:
- prod専用の通知先（Slack botを使うか、webhookにするか）
- Secret rotationとバックアップ
- Alertmanagerの状態をemptyDir/PVCのどちらに置くか
- 通知receiverが不可のときの検知手段（O5との役割分担）
- 個人情報／内部ホスト名を通知先へ流さない制御

**実機受入れの順**: alert rules loaded→`/api/v1/rules`状態確認→
独立したsynthetic negative/positive（実staging障害を起こさない）→
Alertmanager転送→private receiverで実通知とresolved確認→
既存TSDB/Pod/PVC/PV/15resourceの維持。
CIによる勝手な実機alert/通知生成は行わない。

出典:
- https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
- https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/
- https://prometheus.io/docs/alerting/latest/configuration/
