# Pi4 SD Wi-Fi network root image

Pi4 Model B向けの独立した候補イメージ生成処理。既存の有線PXEイメージroleは変更しない。
Ansibleは指定したARM64 builder上でdracut、FAT組立て、最終圧縮と再読込検査を実行する。
実SDへの書込、DHCP変更、既存rootfs変更、再起動は行わない。

## 生成

builderにはPython 3.14、dracut、cpio、kmod、gzip、xz、mtools、util-linuxが必要。
対象と同じARM64 userspaceを実行できるLinux hostを使う。
Pi4のkernel、modules、DTB、start4/fixup4、Wi-Fi firmwareを同じreleaseから準備し、
sysrootはread-onlyでmountする。sysroot内にはPython 3.14、wpa_supplicant、wpa_cli、iw、
systemd-networkd/networkctl、mount.nfs4が必要。networkctlのDHCP JSONはsystemd 261形式を使う。

独立したinventoryに`rpi_sd_image_builders`を定義し、`become`できる接続先を指定する。
実inventoryや認証情報はこのrepositoryへ追加しない。

```yaml
# build-vars.yml: 例示path。実際のmount・保存先へ置き換える。
rpi_sd_wifi_build_enabled: true
rpi_sd_wifi_sysroot: /srv/sd-build/pi4-source-ro
rpi_sd_wifi_tftp_dir: /srv/sd-build/pi4-boot-assets-ro
rpi_sd_wifi_work_root: /srv/sd-build
rpi_sd_wifi_output_root: /srv/sd-artifacts
rpi_sd_wifi_image_id: pi4-wifi-candidate-001
rpi_sd_wifi_kernel_release: 6.18.56-v8+
rpi_sd_wifi_source_revision: REPLACE_WITH_REVIEWED_40_HEX_COMMIT
```

```sh
ansible-playbook -i /path/to/build-inventory.yml \
  ansible/openwrt/playbooks/build-rpi-sd-wifi-image.yml \
  -e @/path/to/build-vars.yml
```

指定commitのpayloadだけを`git archive`で転送する。新しいimage IDと空のwork/outputを必要とし、
同じIDへ上書きしない。remote jobはAnsible asyncで動き、操作端末の切断後もbuilderに残る。
切断時は既存async jobと`artifacts/<image-id>/result.json`を確認し、同じbuildを再投入しない。
source/、runs/、artifacts/はbuilderの永続diskへ置く。

生成物は`<output-root>/<image-id>/`の次のfile。

- `<image-id>.img.xz`: 512 MiBのMBR/FAT32イメージを`xz -9e -T1`で圧縮。
- `manifest.json`: source revision、kernel/modules hash、partition ID、boot file hash。
- `result.json`: compressed/raw size・SHA-256、生成結果。実機受入は別判定。
- `rootfs-kernel-bundle.json`: 対応する専用rootfsへ配置するmarker。
- `SHA256SUMS`: 配布fileのchecksum。詳細な生成ログは`build.log`。

```sh
cd /path/to/artifacts/IMAGE_ID
sha256sum -c SHA256SUMS
xz --test IMAGE_ID.img.xz
xz -dc IMAGE_ID.img.xz | sha256sum
# 最後の値をresult.jsonのraw_sha256と照合する。
```

生成時にはFAT内の全manifest対象fileを再読込し、checksum、partition、空credentialを照合する。
圧縮後も展開streamのsize/hashを未圧縮イメージと比較する。
SDを焼く前に、専用端末のMAC・AP・対応rootfsとserver設定を準備する。

## Wi-Fiとserver側の前提

FAT直下の`wifi.conf`だけを編集する。passwordをGit・会話・診断へ貼らない。

```ini
country=JP
ssid=YOUR_SSID
password=YOUR_PASSWORD
```

WPA2-Personal/CCMP、単一SSID、IPv4が初回対象。`=`以降はそのままの値で、引用符は不要。
SSIDはUTF-8の1〜32 byte、passwordは印字可能ASCIIの8〜63文字。
BOM付きUTF-8・LF・CRLFを受け付け、重複key、未知key、制御文字、空credentialは拒否する。
実credentialと導出PSKはRAM内だけでsupplicantへ渡す。FATのwifi.conf自体は平文なのでSDを管理する。

Wi-Fi MACに対してDHCPで以下を返す。未登録端末や欠落値からdefault rootfsを起動しない。

- Option 17: `192.0.2.10:/exports/pi4-fixed/nfs`のような固定NFSv4 root-path。
- Option 224: `overlay_id=pi4-fixture,roles=base,desktop,stage=stg`。
  `stage=stg`と`overlay_id`を必須とし、同一boot内でroleを固定する。

専用rootfsはNFSv4.2でread-only exportし、`/usr/lib/modules/<exact-release>/`を用意する。
同じkernel/module集合を使うことをserver側で検証してから、生成されたmarkerを
`/usr/lib/sd-netboot/kernel-bundle.json`へ配置する。markerを置くだけでは別kernelとの互換性は成立しない。
rootfsはsystemd、overlay root、保持したnetworkdと共存するように準備する。
NetworkManagerにはboot MACを管理対象外にする設定を渡す。用途は`/etc/sd-netboot-role`で受け取る。
k3s/desktopの自動provisioning、データ永続化、DHCP設定のapplyは本roleに含まない。

## 診断と受入

FATの`diagnostics/boot-status.txt`と`boot-events.jsonl`へ、boot ID、段階、固定reason、
Wi-Fi MACを保存する。前回bootも含め128 KiB未満へ制限し、SSID/password/PSKは含めない。
MACを事前取得できない場合も同じSDで確認・登録できる。
Wi-Fi接続前の失敗は電源停止後にSDからこの2 fileを回収する。
HTTP診断受信口、boot file更新bundle、Pi5、データ用iSCSIは後続対応。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ansible/test_sd_wifi_image.py
# mtoolsを備えた環境ではFATの実書込・再読込検証も実行する。
```

source fixture・ARM64 binary/chroot検査・完成イメージ検査を通しても、Pi4のSDIO、firmware、
実AP認証、lease更新、NFS維持、shutdown、k3s/desktopの実機受入を代替しない。
物理検証は必要なserver設定と候補版が揃ってからまとめて行う。
