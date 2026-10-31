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

## 閉じたartifactのread-only exportと転送確認

`openwrt_security_capture_reader_public_key`に専用Ed25519公開鍵を指定すると、既存の
`/etc/dropbear/authorized_keys`へ管理blockだけを追加する。管理用の既存keyは保持する。
未指定・無効時には自分のblockだけを除去する。private keyをrouterや解析Podへ渡さない。

専用keyでは次の2種類だけを受け付ける。capture IDは固定の時刻/乱数形式で検証する。

```text
manifest <capture-id>
pcap <capture-id>
```

- 強制command、port/agent/X11 forwardingとPTYの禁止を使う。Dropbearの
  [公式manual](https://github.com/mkj/dropbear/blob/main/manpages/dropbear.8)にある明示的な4つの`no-*`制約を指定する。
- server全体の`-c`（keyのcommand上書き）や`-t`（旧版のcommand制約と非互換）がある場合、key追加前に拒否する。
  server設定・SSH serviceは変更しない。実機の専用key接続で任意command・subsystem拒否を別途受け入れる。
- 完了manifest、closed PCAPの全record/size/hash/owner/modeを再検証する。
  `.part`、破損、symlink、別file名、path traversal、任意shell、ACKは専用keyから実行できない。
- read操作は既存lockをread-onlyで開き、state fileやartifactを作成・更新しない。遅いreaderも120秒で打ち切る。
- 転送先はsize/hashを検証し、同一filesystemのrenameとmanifest最後のpublishを完了してからACKする。
  このroleは転送先確認を代行しない。

ACKは既存の管理経路からのみ、`python3 /usr/libexec/homecluster-security-capture.py ack <capture-id> <sha256>`で行う。
受取先の検証済みhashを照合し、そのcaptureの許可fileだけを削除する。未知fileがあれば削除を拒否する。
先に`/srv/security-capture-acknowledged/<id>.json`へreceiptを書き、途中停止後は残りの削除を再開できる。
同一ID/hashの再ACKは冪等、異なるhashは拒否。receiptは72時間、最大64件に制限する。
転送未検証のcaptureを空き容量確保のためにACKしない。

## 認証付きの確認・起動

確認付きの起動は追加のopt-in。自動dispatch、webhook listener、起動timerは追加しない。
次をprivate inventoryで設定する。targetはoperatorが固定BPFと照合して登録する対象名で、
要求からIP、BPF、interface、durationを変更することはできない。

```yaml
openwrt_security_capture_trigger_enabled: true
openwrt_security_capture_trigger_public_key: "<readerと異なる専用Ed25519公開鍵>"
openwrt_security_capture_trigger_targets:
  example-icmp: example-device
```

専用鍵は**guardを検証する信頼済みauthorizer**のcredentialである。
routerのforced commandは固定protocol `homecluster-security-trigger-v1`だけを受け付ける。
鍵の所持者はguardの証明者であり、未信頼のalert送信者へ渡さない。
解析Podやrouterへprivate keyを置かず、既存reader鍵を起動権限へ昇格させない。
SSH認証・暗号化・host key検証を使い、任意shell、転送、PTYを許可しない。

operator側は`scripts/security_capture_trigger.py`を使う。owner-only JSON設定（0600）に
`schema: 1`、`environment: staging`、`automatic: false`と、
`server` / `cluster_uid` / `namespace_uid` / `worker` / `router` / `trigger_key`（絶対path）/
`loki_reader` / `loki_cluster_uid` / `sensor` / `profiles`（許可profile名の配列）を指定する。
serverとloki_readerは既存の管理SSH接続先、UIDは独立に照合したcluster/namespaceのUIDを使う。
このprivate bindingは汎用mechanismと分離し、既存runtime controllerの承認やTUI gateを置き換えない。
receiptはowner-only directory（0700）へ保存する。

```bash
python3 scripts/security_capture_trigger.py plan --config "$TRIGGER_CONFIG" \
  --receipt "$TRIGGER_RECEIPT" --profile example-icmp
# 表示されたprofile/targetとplan IDを確認。plan有効時間は120秒。
python3 scripts/security_capture_trigger.py start --config "$TRIGGER_CONFIG" \
  --receipt "$TRIGGER_RECEIPT" --confirm '<plan-id>'
python3 scripts/security_capture_trigger.py status --config "$TRIGGER_CONFIG"
# 既存security_lab_transfer.pyで、このIDのNAS保存・ACK・解析・配送確認を完了する。
python3 scripts/security_capture_trigger.py settle --config "$TRIGGER_CONFIG" \
  --receipt "$TRIGGER_RECEIPT" --transfer-receipt "$TRANSFER_RECEIPT"
```

- planと直前startでstaging/namespace/worker/coordinator UID、ARM64、pressure、NFS・容量・memory、
  未処理intake/解析owner/Job、3つの固定imageのキャッシュを再確認する。image pullは行わない。
- prod側から最新heartbeatを検索し、180秒以内、parser正常、banIP active、feed検証正常を要求する。
  query失敗、欠落、将来時刻、最新recordの不正値をhealthyへ補完しない。
- routerは固定profile、設定hash、nonce、120秒の要求期限、30秒以内のguard観測、時刻の逆行を検証する。
  JSONの重複keyと非有限数、4096 bytes超、入力待ち10秒超も拒否する。
- `/srv/security-trigger/`へfsyncしてから予約・起動する。同一targetは30分、全体はrolling 3件/時・10件/日。
  global未精算予約は1件。既存capture lock・router負荷・memory・spool・共有storage guardも維持する。
  target割当を変えてcooldownを回避せず、mapping変更はoperatorがreviewする。
- 不明な起動結果を再送しない。予約したcapture IDは起動前に永続化する。
  statusで同じplan/captureを確認し、既存の転送経路から再開する。
  実際に起動しなかった場合、台帳が壊れた場合、解析失敗の場合は証跡を保持してoperatorが調査する。
  台帳の削除や`initialize`によるquota resetを通常の再試行に使わない。
- settleはNAS保存hash、router ACK、成功した解析、宛先UID付きの全event配送を確認して予約だけを解放する。
  回数履歴は保持する。応答を失ってもrouterの最後の精算receiptで同じplanの完了を回収できる。
  監視・解析のguardは観測時点の証拠であり、その後の故障を防ぐ保証ではない。
- 無効化は専用鍵blockと設定だけを除去し、永続台帳を保持する。routing、FW、既存reader鍵を変更しない。
