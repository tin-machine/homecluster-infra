---
status: current
audience: ai
scope: repository
last_reviewed: 2026-06-07
lifecycle: current-context
---

# AI Context

このファイルは、この repository で AI 支援作業を始めるための公開可能な圧縮済み context である。詳細文書は、現在の作業で必要になった範囲だけ読む。

## Repository Goal

この repository の live site に対する最終ゴールは、OpenWrt / PXE Gentoo / k3s / Terraform add-ons が同じ承認済み revision から収束し、k3s 上の monitoring / observability service が起動し、動作確認まで完了している状態である。

完了条件:

- staging k3s の全 node が full power cycle または全 node reboot 後に `Ready` へ戻る。
- kube-system workload が `Running` または `Succeeded` に収束する。
- site-local inventory で tagless Ansible を実行し、OpenWrt / PXE Gentoo / k3s staging の通常収束対象が、明示 tag なしで承認済み revision へ収束する。実行後は k3s node readiness と observability validation まで確認済みである。
- Terraform の `common-crds`、`common-addons`、`common-certificates`、`staging` が site input と state を使って適用済みである。
- observability 用 namespace、object storage、registry、Prometheus、Mimir、Loki、Tempo、Grafana、OpenTelemetry Collector、node-exporter、kube-state-metrics、trace smoke producer が期待状態へ収束する。
- metrics、logs、traces、Grafana datasource、LoadBalancer / service surface を `docs/k3s-observability-validation-checklist.md` に沿って確認済みである。
- k3s の最終完了判定は、monitoring tool、特に Grafana に login でき、datasource、dashboard、Explore、panel error の有無を確認し、実 metrics / logs / traces が利用可能だと検証できた状態とする。credential の実値は repository に残さず、site input または Kubernetes Secret から検証時だけ参照する。

この状態に到達していない間は「完了」と扱わない。実装または site input の不足を修正し、対象 revision を更新し、k3s staging 全体を再起動または power cycle して、起動後の self-convergence と observability validation を繰り返す。停止するのは、同じ blocker が再現し外部入力なしには進めない場合、または destructive / secret-sensitive な操作に明示承認が必要な場合だけである。

## 読む順番

1. `README.md`
2. `docs/README.md`
3. `docs/architecture-decision-record/` 配下の関連 ADR
4. inventory 境界に触る場合は `docs/inventory-storage-options.md`
5. observability validation では `docs/k3s-observability-validation-checklist.md`
6. site-local plan / apply の前には `docs/full-execution-validation.md`
7. OpenWrt live apply では `docs/openwrt-live-apply-plan.md`
8. repository visibility 変更前には `docs/publication-readiness-gate.md`
9. 短い履歴メモが必要な場合は `docs/memory.md`

## Repository の役割

`homecluster-infra` は、OpenWrt / PXE Gentoo / k3s infrastructure の公開可能な subset である。対象範囲は、切り離した sample ではなく、site 値を注入すれば実 site に対して実行できる公開側の正本として維持する。

private workflow は site 値を注入し、pin された public commit SHA が live site で成立することを継続的に検証する。

この repository には、再利用可能な role、Terraform example、公開可能な ADR、example inventory、static check、公開可能な k3s observability validation checklist を含める。実 inventory、secret、Terraform state、kubeconfig、raw operation runbook、troubleshooting log、desktop workload、scanner workload、vendor payload は含めない。

## 現在の境界

- runtime inventory は `../inventory.yml` を期待する。ただし、この file は repository 外で生成または供給する。
- public check は `examples/inventory.yml` を使う。
- secret policy、SOPS recipient、private key、復号済み値、tfvars、kubeconfig はこの repository の外に置く。
- Terraform は root 固有の tfvars JSON を受け取り、staging は chart 固有の site values を受け取る。Terraform は Ansible inventory を解析しない。
- site-specific な Terraform variable と staging Helm site values は、外部入力が無ければ fail closed する。
- PXE Gentoo node の root overlay は意図的に disposable とする。NFS lower rootfs と tmpfs upper/work により、rootfs 差分は reboot で破棄される。再起動後に残すべき state は、root filesystem ではなく、明示した external storage、local block backed filesystem、Terraform state、site input、または configuration management 側へ置く。背景は ADR 0003 と ADR 0006 を参照する。
- boot 後に package を source build する処理は遅くなり得るが、clean boot から再収束できることを優先した結果である。収束時間が問題になる場合は、手動に戻すのではなく、lower rootfs への事前導入、release bundle、binpkg/cache の gate 化として扱う。
- public CI は static check に限定し、repository secrets、self-hosted runner、LAN access、`terraform apply`、実 host 向け Ansible 実行を使わない。
- OpenCode / local LLM agent の tool 制限は、まず repository `opencode.json` の agent
  `permission` で表現する。prompt や wrapper / shell script の制限は補助とし、OpenCode 標準
  permission で `read` / `edit` / `glob` / `grep` / `list` / `bash` / `task` / `webfetch` /
  `websearch` / `lsp` / `skill` を閉じられる場合は `opencode.json` を優先する。
- PXE `ansible-pull` の branch selection は fail closed する。staging は `stg`、production は `main` に mapping し、明示 branch は stage から導出した branch と一致しなければならない。
- PXE SSH host identity は rootfs release から分離する。host key は node ごとの key store に置き、OpenSSH host certificate も併置できる。Host CA private key は PXE client とこの repository に置かない。詳細は ADR 0011 を参照する。
- repository design goal として、tagless Ansible は routine convergence だけを行う。storage partitioning / formatting、rootfs build / replacement、TFTP release switch、sysupgrade、bootstrap package install、k3s runtime storage 初期化や再作成などの destructive / high-blast-radius operation は、`never` tag または同等の明示 gate の下に置き、tagless 実行から到達できないようにする。新しい role がこの種の操作を追加する場合は、tagless 実行に含める前に gate、事前条件、rollback / recovery 手順、post-check を文書化する。
- OpenWrt tagless apply では、`bootstrap_python`、storage、rootfs、sysupgrade を `never` gate に置く。rootfs 内の ansible-pull unit / credential / vars だけを戻す場合は `pxe_ansible_pull` gate を使い、rootfs rebuild や TFTP switch と混ぜない。
- k3s control-plane の `/var/lib/rancher/k3s` が PXE root overlay 上にある場合、power cycle で datastore は再作成される。server data-dir 永続化は observability auto recovery の前提であり、local block または旧 iSCSI 相当を別 gate で成立させる。
- `homecluster-infra` は `k3s_observability_apply` role で Terraform auto-apply service `k3s-ready-terraform.service` を配置する。service は k3s Ready 後に state mount 上の revision-pinned `homecluster-infra` bundle と `site-inputs` bundle を検証し、`common-crds`、`common-addons`、`common-certificates`、`staging` の順で apply する。作業コピーは root overlay を圧迫しないよう state mount 側へ置き、成功後に削除する。

## よく使う確認

```bash
bash scripts/ci/static-check.sh
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 \
  bash scripts/ci/static-check.sh
scripts/docs/context_hygiene_check.py
```

任意実行の Terraform validation は `-backend=false` を使い、site-local state や kubeconfig を使わない。live plan / apply には site-local input が必要であり、`docs/full-execution-validation.md` に従う。

## 公開ドキュメントのルール

documentation address と example 名を使う。実 host 名、private address、serial、MAC address、internal domain、個人 path、private repository URL、token、password、raw log、generated state、vendor payload は追加しない。
