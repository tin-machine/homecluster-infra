# 永続ストレージを使うホスト用 Tailscale

既定は無効。Gentoo PXE ホストの既存 OpenSSH へ tailnet から接続するための role。
Kubernetes Service の公開や k3s の通信経路変更は行わない。

## 構成

1. 新しい lower rootfs の build で `openwrt_gentoo_host_tailscale_enabled: true` を指定する。
    `net-vpn/tailscale` のみ `~arm64` を許可し、既存の Portage/binpkg 導入経路を使う。
    採用する package version、Go dependency、binpkg 適合性は build 前に確認する。
2. 対象 host に `host_tailscale_enabled: true`、`host_tailscale_hostname`、既存の
    `k3s_local_storage_device` を指定する。storage は enabled、force format / ephemeral data は無効にする。
    PXE catalog はこの公開設定だけを client vars に出力する。`ansible_pull.extra_vars.host_tailscale`
    への手書き設定は拒否する。
3. storage unit の Wants から独立した `homecluster-host-tailscale.service` を起動する。
    base が取得済みの source を共通 checkout lock 下で使い、専用 playbook のみ実行する。
    repo の再取得や Galaxy download は行わない。失敗しても k3s / Terraform の OnSuccess を変更しない。
4. `tailscaled` は専用 block device / ext4 / mount root / rw を毎回確認する。
    `/var/lib/rancher/k3s/.homecluster-host-state/tailscale/` は root 専用、state は mode 0600。
    daemon は mount unit に結び付け、mount がなくなれば停止する。通常 boot に登録 key は不要。

`/dev/net/tun` と kernel の TUN driver、既存 package unit との整合性を canary で確認する。
ホスト role は package を emerge しない。導入済み daemon / CLI がなければ終了する。
共有 lower rootfs の `/var/lib/tailscale` に machine identity を配置しない。

## 初回登録

専用 playbook は通常の `site.yml` から呼ばない。管理端末から、inventory 内の実 host 名を
**1台だけ** `--limit` に指定し、明示 tag を付ける。`stage` は `stg` または `prod` が必要。

```bash
ANSIBLE_ROLES_PATH=ansible/arm64/roles ansible-playbook \
  -i /secure/path/inventory.yml ansible/arm64/playbooks/host-tailscale-enroll.yml \
  --limit example-agent --tags host_tailscale_enroll
```

ノード専用の短命・one-off・non-ephemeral tagged Auth Key は、暗号化した inventory から
`host_tailscale_auth_key` として渡す。CLI の `-e` に値を書かない。復号 inventory は mode 0600。
tagOwners、環境別 ACL、既存 OpenSSH の許可、node key expiry は登録前後の管理側確認事項。

- `Running` は key 無しでも skip。`NeedsLogin` / `NoState` のみ登録可能。他の状態は停止する。
- 一時 key は root-only の `/run/host-tailscale-auth-*` に置き、成功・失敗とも `always` で削除する。
- daemon の出力・peer 一覧・credential を task / journal へ転記しない。
- DNS・route 受入・Tailscale SSH・exit node・subnet 広告は無効。
  host netfilter は既定動作なので、canary で既存 k3s / Flannel / LAN を前後比較する。
- 登録済み host の hostname / policy 変更や logout はこの登録 playbook の範囲外。

## 検証と rollback

`python3 scripts/ci/check-host-tailscale.py` を `scripts/ci/static-check.sh` から実行する。
source fixture に加え、canary の tailnet ping / OpenSSH、既存 LAN、DNS、k3s / Pod、
再実行時の changed=0、cold boot 前後の machine identity 一致を実機で受け入れる。
staging の acceptance 後に production 専用 release へ展開する。

SQLite backup は DB/token の明示 allow-list を使う。この state はその backup に含まれない。
disk format / data-dir 全削除とは両立しない。将来の storage repair も state の保持を必須にする。
rollback は LAN から daemon を停止し、host opt-in と release を戻す。
永続 state の削除、logout、管理画面での machine 削除は別の判断とする。

## 上流仕様

- [daemon state の保存先](https://tailscale.com/docs/reference/tailscaled)
- [CLI の登録・network flags](https://tailscale.com/docs/reference/tailscale-cli)
- [Gentoo package source](https://gitweb.gentoo.org/repo/gentoo.git/tree/net-vpn/tailscale)
