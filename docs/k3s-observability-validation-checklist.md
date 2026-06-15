---
status: current
audience: operator
scope: k3s-observability-validation
last_reviewed: 2026-06-05
---

# k3s Observability 検証チェックリスト

## 目的

この checklist は、`terraform/env/staging` と `terraform/modules/observability_stack` が管理する公開可能な observability layer を検証する。

これは live runbook ではない。実 host 名、private address、kubeconfig path、credential、raw log、dashboard screenshot、site-local value は意図的に扱わない。それらは private runbook に置く。

## 対象範囲

staging Terraform example は次を作成する。

| 構成要素 | 管理対象 |
| --- | --- |
| MinIO | observability data 用の S3-compatible object storage |
| Prometheus | metrics scrape と remote write |
| Mimir | 長期 metrics storage と query |
| Loki | log storage と query |
| Tempo | trace storage と query |
| Grafana | dashboard と datasource |
| OpenTelemetry Collector | Pod log collection、syslog intake、OTLP intake |
| node-exporter | node metrics |
| kube-state-metrics | Kubernetes object metrics |
| trace smoke CronJob | 定期 OTLP trace producer |
| OTel Collector RBAC | Kubernetes discovery 用 read-only watch/list/get |

この repository の staging default 名は次の通り。

```bash
export OBS_NS="${OBS_NS:-observability-stg}"
export OBJ_NS="${OBJ_NS:-object-storage-stg}"
export RELEASE_PREFIX="${RELEASE_PREFIX:-stg}"
export TENANT_ID="${TENANT_ID:-staging}"
```

site がこれらの値を変える場合、この checklist を編集せず site-local Terraform variable を使う。

## 前提

- `KUBECONFIG` が対象 cluster を指している、または local operator が既に動作する `kubectl` context を持っている。
- Helm と Terraform state access はこの repository の外から供給する。
- sensitive Terraform variable は private store または local environment から供給し、commit 済み `*.tfvars` file からは供給しない。
- cluster には動作する LoadBalancer 実装と StorageClass がある。
- public CI はこの checklist を実行しない。これは local または private operator validation 専用である。

## Kubernetes health 確認

まずこれを実行する。secret 値を出力しないこと。

```bash
kubectl get nodes -o wide
kubectl get ns "${OBS_NS}" "${OBJ_NS}"
kubectl -n "${OBS_NS}" get pods -o wide
kubectl -n "${OBJ_NS}" get pods -o wide
kubectl -n "${OBS_NS}" get pvc
kubectl -n "${OBJ_NS}" get pvc
kubectl -n "${OBS_NS}" get svc,endpointslices
kubectl -n "${OBJ_NS}" get svc,endpointslices
```

期待結果:

- staging workload に必要な node が `Ready`。
- observability pod は `Running`、短命 job は `Completed`。
- `CrashLoopBackOff`、`ImagePullBackOff`、`Evicted`、長時間の `Pending` pod が残っていない。
- PVC は `Bound`。
- Grafana と OTel syslog service が site で期待する LoadBalancer allocation を持つ。実 address をこの repository に書かない。
- cluster 収束後に restart count が増え続けていない。

cluster が直近で reboot した場合は、application layer を debug する前に node pressure を確認する。

```bash
kubectl describe node | sed -n '/Conditions:/,/Addresses:/p'
kubectl top nodes
kubectl top pods -A --sort-by=memory | head -30
```

## Helm と Terraform の surface 確認

期待する release が存在し、Terraform が作成する object が存在することを確認する。

```bash
helm -n "${OBS_NS}" list
helm -n "${OBJ_NS}" list

kubectl -n "${OBS_NS}" get secret "${RELEASE_PREFIX}-grafana-admin"
kubectl -n "${OBS_NS}" get configmap -l grafana_dashboard=observability
kubectl get clusterrole "${RELEASE_PREFIX}-otel-collector" -o yaml
kubectl get clusterrolebinding "${RELEASE_PREFIX}-otel-collector" -o yaml
```

期待結果:

- Grafana、Prometheus、Mimir、Loki、Tempo、OTel Collector、node-exporter、kube-state-metrics、MinIO の Helm release が存在する。
- site が Terraform 管理の Grafana admin credential を使う場合、Grafana admin Secret が存在する。Secret data は出力しない。
- `clusters/homelab/apps/base/grafana-dashboards/` 配下に dashboard file がある場合、Dashboard ConfigMap が存在する。
- OTel Collector RBAC は read-only で、Kubernetes discovery に必要な `pods`、`services`、`endpoints`、`nodes`、`endpointslices` に限定されている。

Terraform check は local または private に留める。

```bash
terraform -chdir=terraform/env/staging fmt -check
terraform -chdir=terraform/env/staging validate
terraform -chdir=terraform/env/staging plan
```

plan output、state、kubeconfig、local variable file は commit しない。

## Metrics pipeline 確認

Prometheus は target を scrape し、`TENANT_ID` で表される tenant header を使って Mimir へ remote write する。

```bash
kubectl -n "${OBS_NS}" exec deploy/"${RELEASE_PREFIX}-grafana" -c grafana -- \
  curl -fsS \
  "http://${RELEASE_PREFIX}-prometheus-server.${OBS_NS}.svc.cluster.local/api/v1/query?query=up"

kubectl -n "${OBS_NS}" exec deploy/"${RELEASE_PREFIX}-grafana" -c grafana -- \
  curl -fsS -H "X-Scope-OrgID: ${TENANT_ID}" \
  "http://${RELEASE_PREFIX}-mimir-query-frontend.${OBS_NS}.svc.cluster.local:8080/prometheus/api/v1/query?query=up"
```

期待結果:

- Prometheus `up` が空でない結果を返す。
- `X-Scope-OrgID` が environment tenant に設定されている場合、Mimir query が成功する。
- Mimir multitenancy が有効な場合、tenant header なしの query は失敗することがある。これは期待される挙動である。
- router exporter など site-local scrape target は address が site-specific なので private runbook から確認する。

## Logs pipeline 確認

raw log を public artifact へコピーせずに、OTel Collector と Loki の health を確認する。

```bash
kubectl -n "${OBS_NS}" logs \
  daemonset/"${RELEASE_PREFIX}-otel-collector-opentelemetry-collector-agent" \
  --tail=100

kubectl -n "${OBS_NS}" logs statefulset/loki-write --tail=100
kubectl -n "${OBS_NS}" logs statefulset/loki-backend --tail=100

kubectl -n "${OBS_NS}" exec deploy/"${RELEASE_PREFIX}-grafana" -c grafana -- \
  curl -fsS \
  "http://loki-gateway.${OBS_NS}.svc.cluster.local/loki/api/v1/labels"
```

期待結果:

- OTel Collector が export failure、permission error、receiver startup failure を繰り返していない。
- Loki write/backend pod が object storage error や index error を繰り返していない。
- Loki label query が成功する。
- syslog source address と message body は private runbook の material とする。

## Traces pipeline 確認

module は `${RELEASE_PREFIX}-trace-smoke-producer` という軽量 CronJob を作成する。

```bash
kubectl -n "${OBS_NS}" get cronjob "${RELEASE_PREFIX}-trace-smoke-producer"
kubectl -n "${OBS_NS}" get jobs \
  -l app.kubernetes.io/name=trace-smoke-producer \
  --sort-by=.metadata.creationTimestamp

kubectl -n "${OBS_NS}" exec deploy/"${RELEASE_PREFIX}-grafana" -c grafana -- \
  curl -fsS \
  "http://${RELEASE_PREFIX}-tempo.${OBS_NS}.svc.cluster.local:3200/ready"
```

期待結果:

- CronJob が schedule どおり実行される。
- 直近 job が成功完了する。
- Tempo readiness が成功する。
- Grafana Tempo search で environment の smoke service name を見つけられる。screenshot と trace payload はこの repository に入れない。

## Grafana surface 確認

Grafana で user-facing layer を確認する。ただし credential と screenshot は private に保つ。

期待結果:

- site-local credential で Grafana login が成功する。
- Prometheus または Mimir、Loki、Tempo の datasource が healthy。
- Kubernetes、node-exporter、router/dashboard panel が panel error なしで読み込まれる。
- public documentation に screenshot を使う場合でも、dashboard が private address や hostname を露出していない。

## Reboot convergence 確認

cluster 全体の reboot 後は、次の順で確認する。

1. private runbook から network boot と node readiness を確認する。
2. node pressure、runtime storage、PVC status を確認する。
3. Terraform または Helm reconciliation が完了したことを確認する。
4. この checklist で metrics、logs、traces を確認する。
5. その後に Grafana panel や dashboard を debug する。

startup 中の一時的な restart count だけで failure と扱わない。期待される convergence window の後も増え続ける、または pod が unavailable のまま残る場合に failure と扱う。

## No-Go 条件

次のどれかが真なら、public-facing validation artifact を publish / merge しない。

- 実 host 名、private address、serial、MAC address、credential、raw log、kubeconfig、`*.tfvars`、Terraform state、private path が含まれる。
- public CI が repository secrets、LAN access、self-hosted runner、`terraform apply` を必要とする。
- public workflow が private apply controller、router、cluster、inventory store に到達できる。
- Grafana、Loki、Mimir、Tempo、OTel の check に secret 値の出力が必要になる。
- Terraform plan output に site-local value が含まれ、commit 対象になる。

## 残課題

- production は、この公開可能な subset では意図的に README stub として表現する。
- desktop、document capture、private runbook、local apply controller はこの repository の外に置く。
- site-local value と dashboard validation result は private runbook に置く。
