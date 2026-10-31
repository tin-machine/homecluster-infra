# OpenWrt security heartbeat

Python 3と既存logd転送を使い、procd配下で60秒ごとに小さいJSON heartbeatを送る。
既定は無効。利用時はprivate site inputで次を指定する。

```yaml
openwrt_security_monitor_enabled: true
openwrt_security_monitor_sensor: router-example
openwrt_security_monitor_interval_seconds: 60
```

intervalは60–300秒、sensorは小文字英数字と`_.-`、先頭は英数字の64文字以内。
設定変更時は既に動いているmonitorだけを再起動する。無効化時はmonitor自身を停止・自動起動解除する。

JSONは`security_source=openwrt`、`log_type=heartbeat`、sensor、UTC timestampとbanIP runtimeの状態を含む。
last_runはrouter-local timeからUTCへ変換する。欠落、不正JSON、不正値、時計の逆行をfreshへ補完しない。
banIPのrun時刻はfeed別の取得・検証成功を保証しないため、`banip_feed_verification`はunknownを保つ。
IP/MAC、通信本文、credential、banIP設定全文は出力しない。logger tagは`homecluster-security`。

```sh
python3 ansible/openwrt/roles/openwrt_security_monitor/tests/test_monitor.py
```
