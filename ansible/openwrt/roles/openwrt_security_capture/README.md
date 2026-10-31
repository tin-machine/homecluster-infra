# OpenWrt bounded capture

既定無効の固定profile専用capture。既存syslogやroutingを変更しない。
`openwrt_security_capture_enabled: true`と、1〜8件のprivate profileを設定する。

```yaml
openwrt_security_capture_profiles:
  example-icmp:
    interface: br-lan
    filter: icmp and host 192.0.2.10
    duration_seconds: 60
```

`capture-short example-icmp`が検証済みrequestを作り、procdの1回限りのserviceを起動する。
CLIへ任意BPF、shell、出力先を渡さない。boot自動起動・respawnは無効。
結果は`python3 /usr/libexec/homecluster-security-capture.py status`で確認する。

- 全体lockで同時1件。duration 1〜180秒、1000 packets、snaplen 65535、classic PCAP最大65,551,024 bytes。
- `/srv`が実mountでなければ停止。共有資源用8GiBと、capture最大値+256MiBの余裕を残す。
  MemAvailable 256MiB未満・1分load 2超では開始しない。
- `/srv/security-capture/<id>/`はroot 0700、file 0600。未転送・失敗・中断を合わせて最大2件。
  24時間超の残件も新規開始を停止する。転送確認のないファイルを勝手に消さない。
- tcpdumpはrootで動作し、`-Z root`でowner-only spoolへ保存する。profileと設定はroot専用。
  AF_PACKETの権限を一般ユーザーや解析Podへ渡さない。
- 親が時間制限でSIGINTを送りflushを待つ。10秒で終了しなければ、その子だけを強制停止する。
  kernelのparent-death signalで監督Python異常終了時もその子を停止する。`killall`は使わない。
- 書込中は`.part`。閉じたPCAP全record、size、hashを検証してrenameし、manifestを最後にpublishする。
  incomplete/failedのdirectoryは解析入力として使わない。
- manifestは時刻、profile、interface/linktype、packet/切詰め数、kernel drop、stop理由、SHA-256、
  設定hash、offload設定snapshotを保持する。不明なcounterはnull。offload設定だけで全通信の可視性を保証しない。
- 実行中のrole再適用は拒否する。無効化ではこのserviceを停止して受付用設定を除去し、途中のartifactを保持する。
  request受付後に起動失敗した場合もrequestを保持し、同じ要求を自動retryしない。

source fixtureは合成PCAP、固定入力の不正値、容量/負荷guard、実Linuxの子process停止を試験する。
実tcpdump/procd、SSH切断時の終了、capture対象、転送・受取先のhash/permissionは別の実機受入が必要。
このroleだけでは転送・解析・通知は実行しない。
