---
status: current
audience: ai
scope: repository
last_reviewed: 2026-07-22
lifecycle: current-context
---

# AI Context

このファイルは、このrepositoryでAI支援作業を始めるための公開可能な圧縮済みcontextである。
詳細文書は、現在の作業で必要になった範囲だけ読む。

## Repository Goal

このrepositoryのlive siteに対する最終ゴールは、OpenWrt、PXE Gentoo、k3s、Terraform add-onsが
同じ承認済みrevisionから収束し、k3s上のmonitoring、observability serviceが起動し、動作確認まで
完了している状態である。

完了条件:

- staging k3sの全nodeがfull power cycleまたは全node reboot後に`Ready`へ戻る。
- kube-system workloadが`Running`または`Succeeded`に収束する。
- site-local inventoryでtagless Ansibleを実行し、OpenWrt、PXE Gentoo、k3s stagingの通常収束対象が
  明示tagなしで承認済みrevisionへ収束する。実行後はk3s node readinessとobservability validationまで
  確認済みである。
- Terraformの`common-crds`、`common-addons`、`common-certificates`、`staging`がsite inputとstateを
  使って適用済みである。
- observability用namespace、object storage、registry、Prometheus、Mimir、Loki、Tempo、Grafana、
  OpenTelemetry Collector、node-exporter、kube-state-metrics、trace smoke producerが期待状態へ収束する。
- metrics、logs、traces、Grafana datasource、LoadBalancer、service surfaceを
  `docs/k3s-observability-validation-checklist.md`に沿って確認済みである。
- k3sの最終完了判定は、monitoring tool、特にGrafanaにloginでき、datasource、dashboard、Explore、
  panel errorの有無を確認し、実metrics、logs、tracesが利用可能だと検証できた状態とする。credentialの
  実値はrepositoryに残さず、site inputまたはKubernetes Secretから検証時だけ参照する。

この状態に到達していない間は「完了」と扱わない。実装またはsite inputの不足を修正し、対象revisionを
更新し、k3s staging全体を再起動またはpower cycleして、起動後のself-convergenceとobservability
validationを繰り返す。停止するのは、同じblockerが再現し外部入力なしには進めない場合、または
破壊的、secret-sensitiveな操作に明示承認が必要な場合だけである。

## 読む順番

1. `README.md`
2. `docs/README.md`
3. `docs/architecture-decision-record/`配下の関連ADR
4. inventory境界に触る場合は`docs/inventory-storage-options.md`
5. observability validationでは`docs/k3s-observability-validation-checklist.md`
6. site-local plan、applyの前には`docs/full-execution-validation.md`
7. OpenWrt live applyでは`docs/openwrt-live-apply-plan.md`
8. k3s statusが既知caseを返した場合は`remediation_url`の文書
9. repository visibility変更前には`docs/publication-readiness-gate.md`
10. 短い履歴メモが必要な場合は`docs/memory.md`

## Repositoryの役割

`homecluster-infra`は、OpenWrt、PXE Gentoo、k3s infrastructureの公開可能なsubsetである。対象範囲は、
切り離したsampleではなく、site値を注入すれば実siteに対して実行できる公開側の正本として維持する。

private workflowはsite値を注入し、pinされたpublic commit SHAがlive siteで成立することを継続的に検証する。

このrepositoryには、再利用可能なrole、Terraform example、公開可能なADR、example inventory、static check、
公開可能なk3s observability validation checklist、再現可能なgeneric troubleshootingを含める。実inventory、
secret、Terraform state、kubeconfig、raw operation log、desktop workload、scanner workload、vendor payloadは
含めない。

## 現在の境界

- runtime inventoryは`../inventory.yml`を期待する。ただし、このfileはrepository外で生成または供給する。
- public checkは`examples/inventory.yml`を使う。
- secret policy、SOPS recipient、private key、復号済み値、tfvars、kubeconfigはこのrepositoryの外に置く。
- Terraformはroot固有のtfvars JSONを受け取り、stagingはchart固有のsite valuesを受け取る。Terraformは
  Ansible inventoryを解析しない。
- site-specificなTerraform variableとstaging Helm site valuesは、外部入力がなければfail closedする。
- PXE Gentoo nodeのroot overlayは意図的にdisposableとする。NFS lower rootfsとtmpfs upper、workにより、
  rootfs差分はrebootで破棄される。再起動後に残すべきstateは、root filesystemではなく、明示した
  external storage、local block backed filesystem、Terraform state、site input、configuration management
  側へ置く。背景はADR 0003とADR 0006を参照する。
- boot後にpackageをsource buildする処理は遅くなり得るが、clean bootから再収束できることを優先した結果で
  ある。収束時間が問題になる場合は、手動に戻すのではなく、lower rootfsへの事前導入、release bundle、
  binpkg、cacheのgate化として扱う。
- public CIはstatic checkに限定し、repository secrets、self-hosted runner、LAN access、`terraform apply`、
  実host向けAnsible実行を使わない。
- OpenCode、local LLM agentのtool制限は、まずrepository `opencode.json`のagent `permission`で表現する。
  prompt、wrapper、shell scriptの制限は補助とし、OpenCode標準permissionでtoolを閉じられる場合は
  `opencode.json`を優先する。
- PXE `ansible-pull`のbranch selectionはfail closedする。stagingは`stg`、productionは`main`にmappingし、
  明示branchはstageから導出したbranchと一致しなければならない。
- PXE SSH host identityはrootfs releaseから分離する。host keyはnodeごとのkey storeに置き、OpenSSH host
  certificateも併置できる。Host CA private keyはPXE clientとこのrepositoryに置かない。詳細はADR 0011を
  参照する。
- repository design goalとして、tagless Ansibleはroutine convergenceだけを行う。storage partitioning、
  formatting、rootfs build、replacement、TFTP release switch、sysupgrade、bootstrap package install、k3s
  runtime storage初期化や再作成などのdestructive、high-blast-radius operationは、`never` tagまたは同等の
  明示gateの下に置く。
- OpenWrt tagless applyでは、`bootstrap_python`、storage、rootfs、sysupgradeを`never` gateに置く。
  rootfs内のansible-pull unit、credential、varsだけを戻す場合は`pxe_ansible_pull` gateを使い、rootfs
  rebuildやTFTP switchと混ぜない。
- k3s control-planeの`/var/lib/rancher/k3s`がPXE root overlay上にある場合、power cycleでdatastoreは
  再作成される。server data-dir永続化はobservability auto recoveryの前提であり、local blockまたは旧iSCSI
  相当を別gateで成立させる。
- `homecluster-infra`は`k3s_observability_apply` roleでTerraform auto-apply service
  `k3s-ready-terraform.service`を配置する。serviceはk3s Ready後にstate mount上のrevision-pinned bundleと
  `site-inputs` bundleを検証し、`common-crds`、`common-addons`、`common-certificates`、`staging`の順で
  applyする。

## k3s statusと既知case

現在状態を一度だけread-onlyで確認する場合は次を使う。

```bash
bash scripts/pi-k3s-status
```

`healthy`、`converging`、`blocked`、`unknown`を区別し、独自のkubectl、SSH pollingへ置き換えない。
`remediation_status=matched`なら`remediation_url`をoperatorへ返し、文書を読んでから別workflowを選ぶ。
URLは修復承認ではない。status command自身はlive stateを変更しない。

新しい再現性のある障害を検出対象へ加える場合は、deterministic signal、public troubleshooting文書、
remediation catalog mapping、testを同じ変更へ含める。

## よく使う確認

```bash
bash scripts/pi-k3s-status
bash scripts/pi-k3s-status --json | jq .
bash scripts/ci/static-check.sh
RUN_ANSIBLE_SYNTAX=1 RUN_ANSIBLE_LIST_TASKS=1 RUN_TERRAFORM_VALIDATE=1 \
  bash scripts/ci/static-check.sh
scripts/docs/context_hygiene_check.py
```

任意実行のTerraform validationは`-backend=false`を使い、site-local stateやkubeconfigを使わない。live plan、
applyにはsite-local inputが必要であり、`docs/full-execution-validation.md`に従う。

## 公開ドキュメントのルール

documentation addressとexample名を使う。実host名、private address、serial、MAC address、internal domain、
個人path、private repository URL、token、password、raw log、generated state、vendor payloadは追加しない。
