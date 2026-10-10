# NanoCluster prod O2 Step D — Grafanaのsource-only設計

## 現在の境界

O1（Blackbox / Prometheusと4Gi TSDB）およびO2 Step C（staging
node-exporter 4台の直接scrape）はすでにoperator-controlledな別の経路で管理する。
この文書と `scripts/prod_grafana_render.py` は **追加するGrafana用の5オブジェクトだけ**を
生成する。既存10リソースのConfigMap/Deployment/PVC/PV/NetworkPolicyは
**生成・変更しない**。変更の承認、実機適用、永続的な継続更新ownerは別作業である。

```bash
python3 scripts/prod_grafana_render.py --output /tmp/prod-grafana-source.json
python3 -m unittest discover -s scripts -p 'test_prod_grafana_render.py' -v
```

このコマンドは公開可能なsourceをオフラインでレンダリングするだけで、
Kubernetesへは接続しない。出力先が既存ファイルの場合は拒否する。
上記の `/tmp` はsource-onlyのローカル試験例であり、本番のapply先や
credential格納場所ではない。

## 追加5リソース

- `ConfigMap/prod-grafana-provisioning`：Prometheus datasource、dashboard provider、
  8パネルのstaging overview。YAMLとして読めるJSON内容をConfigMapから読み、
  Grafanaのprovisioning pathにマウントする。手作業でのdashboard編集は永続化しない。
- `Deployment/prod-grafana`：`grafana/grafana:13.2.3`、ARM64を実機pullで検証する。
  1 replica / `Recreate` / non-root UID 472 / read-only root filesystem /
  token automount無効化、コンテナmemory limit 512Mi。
  ConfigMap内容のSHA-256をPod-template annotationに置く。
- `Service/prod-grafana`：`ClusterIP:3000`だけ。Ingress、NodePort、
  LoadBalancer、外部DNS、公開認証proxyは作らない。
- `NetworkPolicy/prod-grafana-restricted`：Podへのingressは空（deny）。
  egressはDNSと同namespaceのprod Prometheus PodへのTCP/9090のみ。
- `NetworkPolicy/prod-prometheus-grafana-ingress`：**追加の**NetworkPolicyとして
  Prometheus PodへのTCP/9090をGrafana Podからだけ許す。
  Kubernetes NetworkPolicyのallowはpolicy間で加算されるので、
  既存の `prod-prometheus-restricted` を編集せずに通信経路を追加できる。
  この追加ポリシーにegress設定はなく、Prometheusのstaging scrape境界は維持する。

## operator-privateなadmin Secret

Secretはこの公開rendererの出力に**含めない**。
Grafanaを起動する前にoperator-ownedな
`observability-prod/prod-grafana-admin` Secretを用意し、
`admin-password`と`secret-key`の2つのキーをowner管理で作成・保管する。
認証は無効化しない：`GF_AUTH_ANONYMOUS_ENABLED=false`、
self-signup disabled、grafana admin passwordはSecret参照。
パスワード・Secret実値・private site path・kubeconfigをpublic repository、
CIログ、PRコメントへコピーしない。Grafanaのデータソースには認証情報不要。

## 初期の状態管理

`/var/lib/grafana` は size-limited `emptyDir`（256Mi）であり、
**SQLite DB・ローカルのセッション／ユーザ設定はPod再作成で失われる**。
初回は再現可能なprovisioning sourceを正とし、UI上のカスタマイズは
永続化しない。「Grafanaの設定データ耐久性」はこのStep Dの
acceptanceに含めず、必要ならPVC/backupを別PRで設計する。
この方式はPrometheusの既存4Gi PVCとは完全に独立し、
Grafanaの再作成によってTSDBを書き換えない。

## operator承認後のlive acceptance（source mergeとは別）

1. private apply-controllerの固定create operationを別PRで実装・レビューし、
   **既存O1/O2の10リソース同一性・prod identity・Secret key存在・新規5
   リソースの不存在**を読み取りで検証してから導入する。
   `kubectl apply`や公開CIからの直接mutationは行わない。
2. 追加ポリシーを含め、5オブジェクトだけをowner-controlledに作成する。
   preflightに失敗した場合は停止する。既存10リソースへの変更と混ぜない。
3. prod Grafana PodがReady、Provisioned Prometheus datasourceに
   TCP/9090で疎通できることを検証する。NetworkPolicyのDNS/ingress/egressと
   port-forwardの到達方法は実機CNIの挙動で確認する。
   外部公開設定を追加しない。必要に応じoperator端末から認証付きport-forwardを利用する。
4. `Staging outside-in (prod monitoring)` ダッシュボード上で
   `up{job="staging-node-exporter"}` が4台とも1、
   CPU busy rate、memory usage、root filesystem availability、uptimeが
   4ノードすべて有効。既存API/TCP probeと
   `scrape_samples_post_metric_relabeling`もパネルで確認。
   Pod再作成の前後にdashboardが再provisionされることを検証する。
5. Grafanaが起動してもPrometheusの `up`、probe、以前のTSDB履歴、
   既存PVC/PV UID、prod/stg Ready・pressureが維持されることを確認する。
   iSCSI session数は直接確認できない限り推測しない。

出典：
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
- [Grafana Docker](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/)
- [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
