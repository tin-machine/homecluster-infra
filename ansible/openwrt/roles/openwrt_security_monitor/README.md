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
banIPのrun時刻はfeed別の取得・検証成功を保証しないため、検証を有効にしていない場合の
`banip_feed_verification`はunknownを保つ。
IP/MAC、通信本文、credential、banIP設定全文は出力しない。logger tagは`homecluster-security`。

`openwrt_banip_feed_verification_enabled: true`と
`openwrt_security_monitor_feed_verification_enabled: true`を組み合わせると、固定helperのread-only
`status`を参照する。ここではHTTPS取得やbanIP reloadを起動しない。
現在のset・定義と保存済みの内容検証を照合し、verified/error/unknown/stale、最終成功からの最大経過秒、
feed/family別の最終検証時刻・件数を追加する。helper不達、型不正、未来時刻、欠落はunknown/NULLのまま扱う。
feedのhash・IP一覧・URLはheartbeatへ含めず、2 feedを追加しても1024 bytes以内を検証する。

Collectorのsecurity overlayは検証済み最大経過秒をnumeric metadataへ渡す。
`verified`と主張するmessageに有効な48時間以内の数値がなければparserで拒否する。
実producerからのJSONをCollector 0.102.1へ流すfixtureで、既存heartbeat・通常kernel logの保持と
不正なverified ageの拒否を確認する。

```sh
python3 ansible/openwrt/roles/openwrt_security_monitor/tests/test_monitor.py
```
