# Production Grafana configuration adoption

## QNAP NASのメトリクスとログ

通常は無効。nas_addressにNASのIPv4、nas_usernameに読み取り専用SNMPv3ユーザー名を指定すると、
SNMP exporterとNAS専用Grafana画面を追加する。既存のdatasource、dashboard UID、PVCは維持する。
このprofileはTS-469 / QTS 4.3.4向け。SNMPv3のnoAuthNoPrivを使い、
exporterの送信先を指定NASの/32・UDP/161へ、HTTP入口をprod Prometheus Podだけへ制限する。
認証・暗号化を有効にする場合は、別途Secret参照を含む設定変更が必要。

MIBの説明と返値の単位が一致しないため、空きメモリとボリューム容量・空き容量をKiBからbyteへ変換する。
総メモリと物理ディスク容量はbyteのまま。現在量へrate()を使わない。
HDD状態はMIBの列挙に従い0=Readyとする。欠測を0や正常状態で補完しない。
別機種・firmwareへ転用する前に実際の返値と単位を確認する。

nas_syslog_enabled=trueでは、明示したsite.prod_metrics_target.nameのnodeにAlloyを配置する。
NASのSyslog Clientから、そのnodeのUDP/31514へRFC3164形式でsystem eventを送る。
最初はsystem eventのみを対象にし、connection logは発生量を確認してから選ぶ。
NodePortはexternalTrafficPolicy=Localで送信元を保持し、指定NASだけを許可する。
collectorのmetrics用ServiceはClusterIP。Loki本体は公開せず、内部TCP/3100へ転送する。
ログの固定labelはenvironment=production、source=qnap-syslog、service_name=qnap-nas。
重大度はlevelへ正規化し、時刻は受信時刻を使う。

UDP配送とPod再作成中の無損失は保証しない。queue・message長・stream数・retryを制限し、永続WALは使わない。
不正形式のUDP datagramは破棄され得る。upstreamのparsing-error counterが0でも無損失と判断しない。
NASの実際のsyslog形式と配送成功は、receiver配置後の実機受入で確認する。

source検証はNAS fixture、公式snmp_exporterの設定読込、公式Alloyを使ったlocalhost UDPから
合成Loki push先へのWarning/Info配送と不正入力拒否、ARM64 image manifestで行う。
prod反映はcluster UIDと保存planを照合した独立した承認後に行う。
新規11リソースはstateless。既存4リソースの設定更新に伴いPrometheusとGrafanaが再起動する。
反映後はNAS scrape、単位、Grafana検索、collector送信・破棄counter、Lokiの増加量、既存UIDとscrapeを確認する。
切り戻しは以前の入力でplanをレビューし、追加したstatelessリソースだけを除去する。
既存監視データの削除やterraform destroyは行わない。

## 既存リソースの管理

This root manages the five existing Grafana resources rendered by
`scripts/prod_grafana_render.py`. With `prometheus_site_path`, it also adopts
three existing Prometheus configuration resources (ConfigMap, Deployment,
NetworkPolicy). The combined renderer reconstructs the O1/O2/OpenWrt scrape
jobs, O3 rule mount and O3B Alertmanager destination from source, without live
configuration as an input. Existing dashboard/data source UIDs, Secret
references and file provisioning are preserved. No Secret values, namespace,
Prometheus storage or staging Helm release are managed here.

1. Inventory the live resources and verify the production cluster identity.
2. Initialize a dedicated private local backend outside the repository. Restrict
    state, backups, variable files and saved plans to the operator. Local backend
    locking applies on that host; use one apply host and serialize changes.
3. Import the existing objects into their matching resource addresses. Do not
    apply a plan that proposes creating replacements for existing resources.
4. Check the baseline plan with `scripts/check_prod_grafana_adoption_plan.py`.
    The manifest provider imports the observed object but leaves its desired
    `manifest` and `field_manager` unset. Its first plan therefore reports updates
    even when live object values are identical. The checker permits only that
    state population for the five objects (eight with `--include-prometheus`), with no unknown values, force conflicts,
    object changes, creates or deletes. The initial apply still needs production
    authorization. Require a no-change plan after adoption.
5. Retire the previous bootstrap/update commands for these eight objects. Record
    the transfer before enabling `mark_terraform_ownership`. Do not run both writers.
    These one-shot updaters are not reconciliation services. Keep other owners for
    alert rules, Alertmanager, Loki, PV/PVC and Secrets.
6. The existing `kubectl-create`/`kubectl-patch` managers can conflict with SSA.
    Run server-side dry-run first. If needed, enable
    `adopt_existing_field_ownership` only for the reviewed migration plan, with
    operator authorization. This sets `force_conflicts=true` for the fixed objects;
    reject unrelated drift before using it. Return it to false immediately after
    the transfer, apply the field-manager setting and require a no-change plan.
7. Review the exact saved plan and production identity before an authorized apply.
    Verify unchanged UIDs, data source health, existing panels and reprovisioning.

Example initialization (all paths and values are operator supplied):

```bash
umask 077
terraform init -backend-config="path=/private/state/prod-grafana.tfstate"
terraform import -var-file=/private/site/prod-grafana.tfvars \
  'kubernetes_manifest.grafana["ConfigMap/prod-grafana-provisioning"]' \
  'apiVersion=v1,kind=ConfigMap,namespace=observability-prod,name=prod-grafana-provisioning'
terraform plan -var-file=/private/site/prod-grafana.tfvars -out=/private/plans/grafana.tfplan
terraform show -json /private/plans/grafana.tfplan | python3 ../../../scripts/check_prod_grafana_adoption_plan.py
```

Import the Deployment (`apps/v1`) into `kubernetes_manifest.workload`, and the
Service (`v1`) and both NetworkPolicies (`networking.k8s.io/v1`) into
`kubernetes_manifest.grafana`, using the same key and identifier pattern.
The included moved blocks preserve earlier imported Deployment addresses.
All ConfigMaps and network rules finish updating before Deployments roll out;
Terraform waits for each rollout, with a five-minute update timeout. Import
only changes Terraform state; applying a saved plan is a separate operation.
Keep state backup and previous source revision for recovery. Never use
`terraform destroy` to undo adoption. `prevent_destroy` is an additional guard,
not permission to remove resource blocks or discard state.

## Shared RAG monitoring

Use an owner-only copy of the existing outside-in site JSON for
`prometheus_site_path`. It contains topology, not credentials. Keep it outside
Git with the state and saved plans. Never reconstruct it silently from live data.
Set `rag_enabled=true`, `rag_address` to the specific private IPv4 of the metrics
listener and `existing_folder_uid` to the existing Staging folder UID from the
Grafana API inventory. Dashboard and data source UIDs remain fixed. The separate
RAG folder/dashboard also have fixed UIDs.

The additional scrape is `desktop-rag`, every 30 seconds, with a 5-second timeout
and a 1,000-sample cap. Only `rag_*` and process start time are retained.
Prometheus egress adds one `/32` on TCP 3102. The external metrics server must
limit source addresses to the actual Prometheus node/SNAT address. MCP stays
local to the RAG host. This LAN metrics connection has no TLS or Bearer token.

The rollout restarts Grafana and Prometheus (single-replica Recreate deployments),
so expect a brief monitoring interruption. The Prometheus PVC and historical
series remain intact. Grafana's internal SQLite uses emptyDir; preserve existing
folder/dashboard UIDs and inventory any UI-only settings before restarting.
The dashboard has four sections: service health, indexing, search, resources.
No observations display as missing data, not zero latency or healthy status.

After apply, verify both rollouts, original object and PVC/PV UIDs, original
three dashboards and two data sources, the RAG scrape, histogram observations,
and fixed folder UIDs. Re-run plan with ownership takeover disabled and require
zero changes. Roll back by restoring the previous source/site settings through
Terraform; do not delete resources, state, or monitoring data. Keep the folder UID
pinned when removing the RAG dashboard so URLs remain stable.

## Production host monitoring

`prod_host_enabled=true` adds one node-exporter DaemonSet and a **Production /
Production hosts** dashboard. The default remains disabled. Set the existing
outside-in site's `prod_metrics_target` explicitly:

```json
"prod_metrics_target": {
  "name": "prod-node-1",
  "target": "prod-node-1.lab.example.invalid:9100",
  "address": "192.0.2.50"
}
```

`name` must match the intended node's `kubernetes.io/hostname` label. The existing
site contract requires four declared staging metric targets. The exporter is
scheduled only on that production node; the host does not need a reboot. Host
network/PID namespaces and a read-only host-root mount provide host metrics,
following the [node-exporter container deployment guidance](https://github.com/prometheus/node_exporter/blob/v1.9.1/README.md#docker).
It binds the node's actual host IP on TCP 9100. It does not listen on every host
interface, install packages, or receive a Kubernetes service-account token.

Prometheus scrapes the DNS name itself. Resolve it from the Prometheus Pod and
verify the result equals `address` before applying. Only the explicit address's
`/32` is added to egress; the source renderer performs no DNS lookup. An address
change therefore requires reviewing both DNS and the egress input. Moving Grafana
or Prometheus does not change the monitored host identity. Existing IPv4-only
`prod_metrics_target` inputs remain supported without `address`.

Review a no-change baseline with the previous inputs, then a saved desired plan
containing only the new exporter and the intended existing configuration updates.
The existing cluster-UID checks and data-resource destruction guards remain.
Terraform waits for the single exporter to become Ready before rolling Grafana
and Prometheus. Verify `up{job="prod-node-exporter"}=1`, host CPU/memory/filesystem
series, the Production dashboard, existing scrapes, and unchanged PVC/PV UIDs.
Missing measurements are shown as unobserved; rate panels need fresh samples.

For rollback, restore the previous site input and disable `prod_host_enabled`,
then review/apply that exact plan. The stateless exporter has its own resource
and may be removed; existing monitoring data/resources keep `prevent_destroy`.
Do not use `terraform destroy`, discard state, or delete persistent monitoring data.
