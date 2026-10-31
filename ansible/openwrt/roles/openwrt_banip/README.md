# banIP の定期更新

`openwrt_banip_refresh_enabled: true`と`openwrt_banip_enabled: true`を指定すると、
毎日04:17（routerのlocal time）に`/etc/init.d/banip reload`を実行するroot cronを管理する。
時刻は`openwrt_banip_refresh_hour`（整数0–23）と`openwrt_banip_refresh_minute`（整数0–59）で変更する。
既定は無効。無効化時はこのroleのjobだけを削除し、他のcronは保持する。

`start` / `restart`は保存済みfeedを復元する場合があるため、定期更新には使わない。
roleの反映時には更新を即時起動しない。設定変更後の最終成功・feed別の失敗は別途確認する。
有効化前に実機のbanIP版とfeed名を確認し、廃止されたfeed名を再追加しない。

check modeではcron差分を確認でき、daemonの起動・reloadは実行しない。
通常実行では必要時だけcronを起動/有効化し、crontab変更時に起動中のcronだけをreloadする。
daemonを停止して無効化する操作はこのroleでは行わない。

隔離fixtureは次で実行する。実Ansibleのcron moduleを偽のcrontabに向け、
既存jobの保持、check mode、冪等性、無効化、不正な時刻の拒否を確認する。

```sh
python3 ansible/openwrt/roles/openwrt_banip/tests/test_refresh.py
```
