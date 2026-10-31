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

## Feed別の内容検証

`openwrt_banip_feed_verification_enabled: true`は、banIPと定期更新を有効にし、
feedを既存の`dshield` / `feodo`の1–2件に限定した場合に使う。既定false。
Python 3のSSL/urllibと有効なCA storeを必要とする。roleは取得やreloadを即時起動しない。

有効時のcronは固定helperの`refresh`を呼ぶ。既存`banip reload`が新しいactive状態へ移るまで
最大120秒待ち、HTTPSで取得した現在のfeedと実nft setの内容を照合する。
helperの`verify`はreloadを行わず同じ照合を実行し、`status`は取得を行わず保存済み記録と現在のsetを確認する。
helperは`/usr/libexec/homecluster-banip-feed-proof.py`、設定は
`/etc/homecluster-security/banip-proof.json`。全actionに`--config`で同設定を渡す。

検証範囲は`independent_https_content_equals_active_nft_set`。
banIP本体のdownload logとは別に、検証済み内容が現在のkernel setと一致した時刻を管理する。
`dshield`はstart/end/prefix length、`feodo`はIPv4単体の形式を検証し、CIDRを正規化して
nftのauto-mergeによる表現差を吸収する。現registryはIPv4だけを扱い、IPv6の検証成功へ拡張しない。
設定済みfeed・stockのURL/rule・active familyが異なれば不明/失敗となる。

取得はTLS検証付きHTTPS、許可hostへのHTTPS redirectだけ、HTTP 200、各1MiB/5000 records以内。
socket/wall timeoutを設け、空・HTML・不正range・reserved prefix・未知nft表現は拒否する。
取得前後のruntime generationとsetが変わった場合も成功にしない。
検証helperはnft policyを変更せず、banIP packageのfileもpatchしない。

記録は`/etc/homecluster-security/banip-proof-state.json`へatomic/fsyncで保存する。
directory 0700、file 0600、所有者・symlink/hardlink・部分書込を検査し、verify/refreshはflockで直列化する。
IP一覧は保存せず、時刻・内容hash・件数・結果だけを残す。取得失敗や内容不一致では最終成功を保持し、
新しい失敗を別に記録する。`status`は実setとの一致と48時間以内の記録を必要とし、古い成功をfreshへ補完しない。
不明なstateや`.part`はoperatorが照合するまで保持する。設定変更でproofの定義hashが変わった場合も再照合が必要。

```sh
python3 ansible/openwrt/roles/openwrt_banip/tests/test_feed_proof.py
```
