---
status: current
audience: ai
scope: repository-memory
last_reviewed: 2026-05-31
lifecycle: memory-index
---

# 履歴メモ

このファイルは、公開可能な短い履歴メモを保持する。raw investigation context と site 固有 operation detail はこの repository の外に置く。

## 2026-05-31

- この repository は、Apache-2.0 license、third-party dashboard 向け NOTICE、公開可能 ADR、example inventory、static check を持つ公開可能な infrastructure subset として準備した。
- inventory 境界は、外部で生成した `../inventory.yml` entrypoint を使う。暗号化された source、SOPS recipient、private key、復号済み値はこの repository の外で管理する。
- 初期 public CI 境界は static check のみにした。対象は redaction scan、hard-exclude file scan、trailing whitespace scan、利用可能な場合の Terraform formatting、local filter plugin の Python syntax check、任意の Ansible syntax check、AI context hygiene である。

## 2026-06-04

- PXE runtime default は fail closed にした。site inventory は PXE server endpoint と ansible-pull repository URL を明示する必要がある。documentation IP range と placeholder repository URL は、rootfs runtime file を生成する前に拒否する。
- verbose Ansible run は、runtime file generation task が保護されていない場合、render 済み group vars を露出し得る。ansible-pull group_vars path は、render 済み値に environment variable、endpoint 名、その他 site-sensitive metadata が含まれ得るため `no_log` を有効に保つ。
- Ansible `no_log` は、managed host の syslog に書かれる module invocation record を抑止しない。OpenWrt と arm64 controller config では `no_target_syslog = True` を設定した。operation summary は値を出さない明示 check を使う。
- PXE runtime generator は、現在管理対象の lower rootfs set に対する stale group/client vars を削除する。historical rollback rootfs tree は標準では一括 rewrite しない。古い tree を boot する前に runtime vars を refresh する。
- DHCP/TFTP path が正常なだけでは、PXE rootfs の正しさは証明できない。preflight layer では generated kernel command line、bootstrap endpoint、ansible-pull unit repository URL、host-specific client vars も確認する必要がある。

## 2026-06-06

- default static check と、任意実行の Ansible syntax、Ansible task listing、backend-free Terraform validation gate を含む網羅的 offline validation が成功した。
- `docs/publication-readiness-gate.md` は、visibility 変更前の最終 checklist を記録する。内容は GitHub metadata target、Actions settings、branch protection と rulesets の扱い、repository surface audit item、private validation prerequisite である。
- PR #1 で branch / PR / Actions / merge path を確認し、`main` へ merge 済み。merge 済み public infra revision は private input revision と bundle `42ffe4d4caaa-a11f0281faca` として staged され、staging apply service は成功した。cluster は 2 Ready node、non-running Pod なしだった。
