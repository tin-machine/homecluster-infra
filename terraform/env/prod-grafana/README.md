# Production Grafana configuration adoption

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
