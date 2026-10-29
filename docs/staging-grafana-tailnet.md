# Staging Grafana over Tailscale

`terraform/env/staging/grafana_tailnet.tf` adds an optional HTTPS Ingress for the existing `observability-stg/stg-grafana` Service. It reuses the staging Tailscale Operator and its precreated `tailscale/operator-oauth` Secret. No OAuth credential is passed through Terraform input or stored in this repository.

Set `tailscale_operator_enabled=true` and `grafana_tailnet_hostname` to an available single MagicDNS label in the private staging site input. The default `null` leaves the Ingress absent. The Ingress uses `ingressClassName=tailscale`, publishes only Grafana port 80, and does not enable Funnel. The existing LoadBalancer service remains available.

Both new resources use the staging OAuth client's existing `tag:cli-workspace-stg` proxy tag. The default `tag:k8s` was rejected by this tailnet during the first staging apply. This tag permits proxy registration only; verify tailnet ACLs separately for Grafana HTTPS and TypeVT port 9100. A dedicated monitoring tag can replace it after the tailnet policy and OAuth client are updated together.

Before applying, confirm the target is the staging cluster, the Operator and OAuth Secret exist, the chosen hostname is free, and the tailnet policy allows only the intended viewers to reach the proxy. Apply the normal staging Terraform plan after the cluster is healthy. Confirm HTTPS certificate issuance, Grafana login and redirect behavior, dashboard/log access, and the existing LAN address. A failed login redirect may require setting Grafana's `root_url` in the staging site values; check both access paths before changing it.

The pinned Operator chart is `1.104.2`; its Serve proxy image is pinned to `v1.104.4`. This changes the Operator Helm release on the next normal apply, even when Grafana Ingress remains disabled. Review the rollout and the existing CLI workspace proxy in the saved plan.

Source checks:

```sh
cd terraform/env/staging
terraform init -backend=false
terraform validate
terraform test -filter=tests/grafana_tailnet.tftest.hcl
terraform test -filter=tests/typevt_tailnet_egress.tftest.hcl
```

## TypeVT metrics over the tailnet

Set `typevt_tailnet_fqdn` to the tablet's full MagicDNS name in private staging site input after its exporter is active. By default the egress Service is absent. When enabled, the Operator creates a standalone egress proxy for `observability-stg/typevt-node-exporter-tailnet` on TCP 9100. This lets a staging Pod reach the exporter without exposing it on the LAN. The Operator rewrites the Service's `externalName`; Terraform ignores only that managed field.

Before adding a Prometheus scrape target, confirm the egress Service reports `TailscaleEgressSvcReady=true` and a bounded test Pod can read `http://typevt-node-exporter-tailnet.observability-stg.svc.cluster.local:9100/metrics`. Confirm the tailnet policy permits this egress proxy to reach TypeVT. Remove the Service or set the variable to `null` to disable the route. See the [Tailscale egress guide](https://tailscale.com/docs/kubernetes-operator/egress/access-tailnet-service) for the Operator's ExternalName behavior.
