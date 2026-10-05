---
status: current
audience: operator-ai
scope: pxe-release-identity
last_reviewed: 2026-09-30
---

# PXE release identity status

`bash scripts/pi-pxe-release-identity-status --candidate YYYYMMDD` は、
standard PXE release identity候補が再利用可能かを一度だけread-onlyで確認する。

このcommandはinventory編集、release作成、TFTP promote、Ansible mutationを行わない。
出力は次のbounded JSONだけとする。

```json
{"candidate":"20990101","reason":"identity_available","status":"pass"}
```

## 判定対象

candidateは次の両方で未使用であり、現行staging日付より新しい必要がある。

1. generated inventoryの `openwrt` groupで、stagingの
    `openwrt_gentoo_release_bundle_stage_dates.stg` と一致しないこと。
2. OpenWrt側で次のimmutable release pathが1つも存在しないこと。
    - `/srv/gentoo/releases/<candidate>.json`
    - `/srv/gentoo/<candidate>-rpi4`
    - `/srv/gentoo/<candidate>-rpi5`
    - `/srv/gentoo/tftp-root/dates/<candidate>-rpi4`
    - `/srv/gentoo/tftp-root/dates/<candidate>-rpi5`

manifestがまだ作られていなくても、partial rootfs / TFTP materializationが存在すれば
`blocked / identity_in_use` とする。既存pathを新しいgenerationで再利用しない。

## fail-closed contract

次は `unknown / source_unavailable` とする。

- generated inventoryを読めない。
- `openwrt` groupがexactly one hostではない。
- staging release identityが欠落または `YYYYMMDD` ではない。
- remote read-only probeが失敗、timeout、または曖昧な結果を返す。

candidate形式が `YYYYMMDD` でない場合は、remote commandを実行せず
`blocked / probe_blocked` とする。

host名、address、inventory pathのcaller override、remote path overrideは公開interfaceに持たない。
標準inventory entrypointはrepository rootから見た `../inventory.yml` に固定する。

## controllerとの境界

このmechanismは「候補が空いているか」だけを判定する。
候補日の探索順序、search window、next-day policy、plan identity、operator approvalは
private controller側のpolicyであり、このrepositoryへ複製しない。

public CIではreal inventoryやLANへ接続せず、
`scripts/ci/check-pxe-release-identity-status.py` のfixtureだけを実行する。
