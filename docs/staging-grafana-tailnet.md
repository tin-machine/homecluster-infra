# Staging Grafana over Tailscale

`terraform/env/staging/grafana_tailnet.tf` adds an optional HTTPS Ingress for the existing `observability-stg/stg-grafana` Service. It reuses the staging Tailscale Operator and its precreated `tailscale/operator-oauth` Secret. No OAuth credential is passed through Terraform input or stored in this repository.

Set `tailscale_operator_enabled=true` and `grafana_tailnet_hostname` to an available single MagicDNS label in the private staging site input. The default `null` leaves the Ingress absent. The Ingress uses `ingressClassName=tailscale`, publishes only Grafana port 80, and does not enable Funnel. The existing LoadBalancer service remains available.

Before applying, confirm the target is the staging cluster, the Operator and OAuth Secret exist, the chosen hostname is free, and the tailnet policy allows only the intended viewers to reach the proxy. Apply the normal staging Terraform plan after the cluster is healthy. Confirm HTTPS certificate issuance, Grafana login and redirect behavior, dashboard/log access, and the existing LAN address. A failed login redirect may require setting Grafana's `root_url` in the staging site values; check both access paths before changing it.

The pinned Operator chart is `1.104.2`; its Serve proxy image is pinned to `v1.104.4`. This changes the Operator Helm release on the next normal apply, even when Grafana Ingress remains disabled. Review the rollout and the existing CLI workspace proxy in the saved plan.

Source checks:

```sh
cd terraform/env/staging
terraform init -backend=false
terraform validate
terraform test -filter=tests/grafana_tailnet.tftest.hcl
```
