# Production monitoring portal

This separate Terraform root manages a small static Nginx link page and, when enabled, Tailscale HTTPS Ingresses for the page and the existing production Grafana Service. It does not manage Grafana, Prometheus, Loki, their Secrets, or the staging cluster.

`portal_site_path` points to an owner-only JSON file outside the repository. It contains exactly `prod_grafana_url`, `stg_grafana_url`, `portal_hostname`, and `prod_grafana_hostname`. Both URLs must be bare `https://<device>.<tailnet>.ts.net` addresses with no path, query, or credentials. The renderer escapes HTML output and rejects a site file readable by other users. Keep this file and Terraform state in the private work area.

`enable_tailnet=false` is the default. It leaves both Ingresses and the Tailscale Operator absent while allowing a source review of the portal manifest. Enabling tailnet requires the precreated `tailscale/operator-oauth` Secret containing `client_id` and `client_secret`; no credential is passed in Terraform variables or Helm values. Create that production Secret only after staging HTTPS, access policy, Grafana login, and the saved production plan have been accepted. The Operator chart is pinned to `1.104.2` and its Serve proxy image to `v1.104.4`.

The root requires an explicit production kubeconfig context and expected `kube-system` UID. Confirm both before planning. Review the exact ConfigMap, Deployment, Service, NetworkPolicy, Ingress, and Helm changes in a saved plan before apply. The existing Grafana NetworkPolicy remains in place; this root only adds ingress from the `tailscale` namespace to port 3000. The Nginx Pod has a read-only root filesystem, no service account token, no egress, and a single 8080 listener.

Source checks:

```sh
python3 -m unittest discover -s scripts -p 'test_prod_monitoring_portal.py'
cd terraform/env/prod-monitoring-portal
terraform init -backend=false
terraform validate
```
