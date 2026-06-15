---
status: current
audience: operator
scope: publication-readiness
last_reviewed: 2026-06-15
---

# 公開 readiness gate

## 目的

この gate は、repository visibility を変更する前の最終 checklist である。

この文書だけでは visibility 変更を承認しない。visibility 変更は、source check、GitHub settings、private input binding、staging convergence をすべて review した後に、人間が別途 go/no-go 判断する。

## 現状

- repository visibility はまだ private。
- Gate 1 offline validation は 2026-06-15 に成功した。
  - `bash scripts/ci/static-check.sh`
  - `STATIC_CHECK_STRICT_LOCAL=1 bash scripts/ci/static-check.sh`
  - `RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 bash scripts/ci/static-check.sh`
- 含まれる実装は、private input を注入すれば実 site に対して実行可能な状態を維持する。切り離した sample ではない。
- site-local plan、apply、reboot convergence、idempotency は private operator workflow task として扱う。
- PR #1 で branch / pull request / GitHub Actions / merge path を検証した。`static-check` workflow は成功し、PR は `main` に merge 済み。
- merge 済み commit を private input revision と bundle として staged する経路を確認済み。
- 2026-06-15 に public infra commit `11346a3` と private input revision `1f27a1e` を staging bundle として反映し、OpenWrt PXE ansible-pull gate、SwitchBot off/on、PXE boot、k3s staging、observability smoke まで確認した。
- 2026-06-15 の controlled convergence では、control-plane 1 台、agent 3 台が Ready になり、observability Pod は期待状態へ収束した。distcc 対象 node は `distccd` が enabled / active / listen 済みで、Prometheus / Tempo / trace smoke も成功した。
- OpenWrt は PXE ansible-pull / release staging path まで確認済み。tagless apply は storage、rootfs、TFTP、sysupgrade path を含み得るため、public 化前の別 gate として扱う。
- 2026-06-06 には public infra commit と private input commit を staging bundle として pin し、`common-crds`、`common-addons`、`common-certificates`、`staging` の Terraform plan がすべて差分なしであることを確認済み。commit が増えたため、visibility 変更直前の最終 commit で同じ no-diff plan をやり直す。
- GitHub API / UI surface audit の最終記録は 2026-06-06 であり、2026-06-15 の作業では再監査していない。visibility 変更直前に secrets / variables / webhooks / branch protection / rulesets を再確認する。

## GitHub metadata の目標

public visibility 前に repository metadata を次の状態にする。

これらの GitHub repository settings の実設定は、private operator repository 側の Terraform で管理する。`homecluster-infra` には期待状態と gate だけを置き、GitHub admin token、Terraform state、saved plan、apply log は置かない。

| 項目 | 目標 |
| --- | --- |
| Description | `Public-safe executable OpenWrt / PXE Gentoo / k3s homelab infrastructure` |
| Topics | `homelab`, `openwrt`, `pxe`, `gentoo`, `k3s`, `terraform`, `ansible`, `raspberry-pi`, `observability`, `infrastructure-as-code` |
| Homepage | public Hugo entry ができるまでは空にする。できた後、その canonical URL を設定する。 |
| License | `LICENSE` による Apache-2.0。 |
| NOTICE | third-party dashboard attribution のため `NOTICE` を維持する。 |
| Issues | maintainer が read-only publication を望む場合を除き enabled。 |
| Wiki | disabled。 |
| Projects | 明示的に使う場合を除き disabled。 |
| Discussions | support venue が必要な場合を除き disabled。 |

README には `static-check` badge がある。redaction scan は、この repository 自身の public GitHub URL だけを許可する。他の site-local URL や private repository URL は redaction finding として扱う。

## GitHub Actions settings

ADR 0009 の public CI 境界に従う。

目標設定:

- GitHub Actions は enabled。ただし、この repository で必要な workflow/action に限定する。
- Allowed actions は GitHub-owned actions のみ、または同等に狭い selected actions allowlist。
- setting が利用可能なら、external actions には full-length commit SHA pinning を要求する。
- Default workflow permissions は read repository contents。
- GitHub Actions による pull request 作成または approve は disabled。
- この public repository に self-hosted runner は置かない。
- public CI に repository secrets / variables は不要。
- artifact/log retention は短く保つ。private validation output を publish しない。

参考:

- GitHub Actions repository settings:
  <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository>
- GitHub Actions permissions API:
  <https://docs.github.com/rest/actions/permissions>

## Branch protection と rulesets

`main` は public operation では保護されている必要がある。

最低限の branch protection:

- merge 前に pull request を要求する。
- merge 前に status checks の成功を要求する。
- required check は `static-check`。
- merge 前に branch が最新であることを要求する。
- force push を禁止する。
- branch deletion を禁止する。
- 利用可能なら conversation resolution を要求する。

Rulesets は有用だが、plan によっては private repository warning が出る。GitHub docs では、rulesets は GitHub Free の public repository と、GitHub Pro / GitHub Team / GitHub Enterprise Cloud の public / private repository で利用可能とされている。

この gate では次のように扱う。

- repository がまだ private で、plan limitation により `protected: false` が返る場合、それは UI target settings を skipped した証明ではなく plan limitation として記録する。
- private-repository ruleset warning は記録する。この warning だけでは gate を block しない。
- public visibility 変更と branch protection re-check を、1 つの final controlled procedure として扱う。
- visibility 変更後、`branches/main` が `protected: true` を返すまで、external pull request や routine change を受け入れない。
- public 前に private enforcement が必要な場合は、private branch protection / ruleset enforcement を support する plan へ移す。

1人運用では `required_approving_review_count = 0` とし、`require_last_push_approval` は無効にする。レビュー必須にする場合は、自分以外の承認者または bot / maintainer 運用を先に用意する。

push ruleset は public visibility gate の主防御にしない。GitHub docs では push ruleset は private / internal repository 向けの push protection として説明されているため、public 化後の主防御は `static-check` required status check、branch ruleset、Secret Protection / user push protection、local redaction scan とする。private Terraform 側には optional な push ruleset を置けるが、GitHub 側で対象 repository に enforcement されることを確認するまで有効化しない。

参考:

- Rulesets availability:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets>
- Protected branches:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>

## Repository surface 監査

public visibility 前に、API または UI で次を確認する。

- `main` protection が active。
- repository rulesets が記録され、enforcement state が理解されている。
- Actions permissions がこの gate と一致している。
- required status check 名が正確に `static-check`。
- Environments は意図的に使う場合を除き空。
- Deploy keys は意図的に使う場合を除き空。
- Webhooks は意図的に使う場合を除き空。
- Repository secrets は空。
- Repository variables は空。
- Actions caches が private material を含まない。
- open pull request が private value、raw log、tfvars、state、kubeconfig、private repository URL、vendor payload detail を含まない。

現在の token がいずれかの surface を読めない場合は `API 403` と記録し、GitHub UI または short-lived token permission で確認する。routine audit のためだけに broad write permission を保持しない。

### 2026-06-06 API 監査

2026-06-06 の read-only API check では次を確認した。

| 確認面 | 結果 |
| --- | --- |
| Repository visibility | private |
| Default branch | `main` |
| Actions enabled | yes |
| Allowed actions | selected |
| Selected actions | GitHub-owned actions allowed、verified / pattern / explicit external actions は not allowed |
| Full-length SHA pinning | 現在の API response では露出しない。UI で setting が見える場合は確認項目として残す。 |
| Workflow permissions | read |
| Actions PR approval | disabled |
| Self-hosted runners | 0 |
| Environments | 0 |
| Deploy keys | 0 |
| Actions caches | 0 |
| Open pull requests | 0 |
| Branch protection API | private repository plan limitation により 403。visibility 変更前に UI で確認し、public visibility 後に再確認する。 |
| Branch protected flag | 現在の plan limitation 下では private の間 `false` |
| Rulesets API | private repository plan limitation により 403。public visibility 後、または paid-plan migration 後に再確認する。 |
| Repository secrets API | current token では 403。visibility 変更前に UI または short-lived read permission で 0 件を確認する。 |
| Repository variables API | current token では 403。visibility 変更前に UI または short-lived read permission で 0 件を確認する。 |
| Webhooks API | current token では 403。visibility 変更前に UI または short-lived read permission で 0 件を確認する。 |

2026-06-06 に short-lived repository Administration write permission で metadata を更新した。

- Description は target と一致。
- Topics は target と一致。
- Issues は enabled。
- Wiki、Projects、Discussions は disabled。
- `delete_branch_on_merge` は enabled。

current token では、repository Actions secrets、repository Actions variables、webhooks はまだ読めない。これは 0 件確認ではない。GitHub UI で確認するか、同じ repository-scoped token に `Secrets: read`、`Variables: read`、`Webhooks: read` を一時付与し、count だけを記録する。read-only audit work に write permission は付けない。

## Visibility 変更前の private validation

exact public commit SHA に対して private operator workflow を実行する。

1. public infra commit と private input commit を 1 つの bundle に束ねる。
2. external inventory を render / validate する。
3. explicit limit を付けて、site-local Ansible `--syntax-check`、`--list-hosts`、`--list-tasks` を実行する。
4. real state と private input file で Terraform plan を実行する。
5. plan review が通った後、failure domain ごとに apply する。
6. 意図した staging node を reboot または power-cycle する。
7. k3s node、add-on Pod、storage mount、networking、observability を確認する。
8. scoped apply を再実行し、idempotency を確認する。

private plan output、raw log、host 名、address、secret 名はこの repository にコピーしない。

2026-06-06 の staging validation では、k3s agent join と networking の live convergence を実施した。検証中に見つかった k3s local storage、agent join scope、k3s version pinning、nullable network input、Wi-Fi power-save best-effort、flannel interface fallback、Ansible fact 参照の問題は source 側で修正した。

ただし、この結果は OpenWrt tagless apply を承認するものではない。OpenWrt の storage / rootfs / TFTP / sysupgrade は、router rollback point と切り戻し手順を持つ別 gate で扱う。

Terraform は final commit bundle の `common-crds`、`common-addons`、`common-certificates`、`staging` を live state と private input で plan し、いずれも差分なしで停止する。plan output は private operator storage に保存し、この repository へコピーしない。

2026-06-15 の staging validation では、staging PXE release を `20260601` へ進め、OpenWrt で PXE ansible-pull vars を反映した後に SwitchBot off/on を実施した。全 staging node は対象 rootfs で boot し、base / k3s / Terraform apply chain は成功した。k3s は 4 node Ready、observability stack は Prometheus、Tempo、trace smoke まで成功した。maturin は公式 Gentoo binhost から binary merge され、rootfs の glibc 更新は要求されなかった。

この結果は staged bundle の実行性を示すが、visibility 変更を単独で承認するものではない。公開直前の final commit に対して、GitHub surface audit、private no-diff Terraform plan、必要な scoped apply / idempotency check を再実行する。

## Go / No-Go

次をすべて満たす場合だけ Go とする。

- publish 対象 commit で Local Gate 1 が green。
- final publication gate changes を入れた PR で CI が green。
- GitHub metadata と settings がこの文書に一致する、または deviation が記録されている。
- private validation により、同じ public commit が実行可能なままであることを確認済み。
- repository surface audit item に unknown が無い。ただし、public visibility 直後に再確認することを明示した private-plan limitation は例外とする。
- 最終 action は visibility change だけ。

次の場合は No-go とする。

- secret、tfstate、kubeconfig、private repository URL、real inventory、vendor payload、raw troubleshooting log が見つかった。
- public CI が private network resource に到達できる。
- public visibility 後に required branch protection が無い、または post-public protection check が final procedure に含まれていない。
- private validation が local uncommitted change に依存している。
