# prod Grafana での OpenWrt メトリクス

prod Prometheus は OpenWrt の node exporter 1 台を直接 scrape できる。private な
outside-in site input の `openwrt_metrics_target` で有効にし、staging 4 台の
`metrics_targets` とは分ける。

```json
"openwrt_metrics_target": {"name": "example-router", "target": "192.0.2.100:9100"}
```

target は IPv4 address の 9100 番 port に限定する。renderer は
`openwrt-node-exporter` job と対象 `/32` にだけ向けた Prometheus egress rule を追加する。
staging の scrape job、alert rule、Prometheus PVC は維持する。Grafana には
prod Prometheus datasource を使う独立した OpenWrt dashboard を追加し、scrape 状態、
CPU、memory、load、network traffic を表示する。

private site input と rendered manifest は repository 外に置く。live 更新前に prod host から
OpenWrt `/metrics` の到達性と prod Prometheus の network path を確認する。既存 prod resource
の更新には operator 承認、prestate の保存、更新後の scrape と dashboard の受入が必要。
