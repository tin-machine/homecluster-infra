# Production outside-in monitoring MVP (O0/O1)

Status: source-only candidate; no runtime apply, credentials, or private inventory.

This public mechanism is for a small, stable **production k3s** cluster to observe a
separate, experimental **staging k3s** cluster. It deliberately does not install
the existing staging Terraform observability stack. That stack also contains
Mimir, Loki, Tempo, object storage, and additional controllers, which are not
needed to start detecting the staging API's failure.

## O0 — discovery required before operator-authorized deployment

Gather the following **read-only** observations using existing private operator
entrypoints. Do not copy resulting hostnames, IP addresses, kubeconfigs, bearer
tokens, Terraform state, or full output into this public repository.

| Surface | Observation and decision |
| --- | --- |
| Production identity | Confirm current deployment context identifies the intended production cluster and the server is Ready; use the existing k3s status authority for staging rather than creating a second mutation path. |
| Storage | Verify the production datastore mount is persistent and stable; inspect node capacity and DiskPressure. No storage reformat, target reload, or LUN login changes. |
| Staging API | Determine externally reachable HTTPS API address, certificate SAN, CA trust, and whether unauthenticated GET /readyz is allowed. Record only the approved private target in the private site input. |
| Staging node probes | Determine whether TCP port 9100 is actually reachable from the production Pod network. Absence is not an excuse to change staging exporters in this PR: leave tcp_targets empty. |
| Routing and policy | Confirm Pod-source egress, DNS, port 6443 or 443, and required allowlisted CIDRs. Check the k3s network-policy controller is enforcing NetworkPolicy before trusting it as a security boundary. |
| Workloads | Note existing Prometheus/OTel/exporter ownership on staging without changing existing scrape, Services, chart values, or state. |
| Images | Confirm both pinned image tags have arm64 manifests and that the production registry route can pull them. |
| Apply ownership | Identify the existing approved private production apply entrypoint; do not run the rendered output through the staging Terraform apply process. |

The source inventory shows that the existing public
`terraform/modules/observability_stack` is staging-oriented. The
`terraform/env/production` directory is intentionally a placeholder.
This MVP therefore renders standalone Kubernetes objects that an authorized
private production deployment workflow can review and apply separately.

## Optional staging API readiness authentication (source-only, no rollout)

Kubernetes v1.34+ supports endpoint-scoped anonymous authentication using an
`AuthenticationConfiguration`. A controlled staging-only opt-in is implemented
by the public Ansible `k3s_networking` role:

```yaml
# Set this only for the selected staging k3s server in private inventory,
# after source review and operator-authorized maintenance planning.
k3s_stg_readyz_anonymous_enabled: true
```

The default is **false**. This public source change alone does not change a live
API server. The role checks `stage=stg`, control-plane role, the downloaded
K3s version (v1.34+), and the absence of other `kube-apiserver-arg` source
values before staging two files in the root filesystem:

- `/etc/rancher/k3s/authentication-stg-readyz.yaml`:
  `anonymous.enabled=true`, with **only** `conditions: [{path: /readyz}]`.
- `/etc/rancher/k3s/config.yaml`: an additional
  `kube-apiserver-arg: [authentication-config=/etc/rancher/k3s/authentication-stg-readyz.yaml]`
  when the opt-in is enabled.

In the staging site playbook, `k3s_defer_service_restart=true`, so writing
the files does **not** restart the running k3s API server. An operator-controlled
restart or cold boot is required before this setting takes effect. On PXE
overlay-root nodes, both files must be recreated by the Ansible convergence
path at every boot **before** the k3s server starts. Confirm the
`authentication-config` file survives that sequence in the *effective* overlay
and that the effective flags do not also include `--anonymous-auth=false`.
Do not deploy only the config-file reference without its target file.

This does **not** intentionally grant anonymous access to `/api` or `/apis`.
Authentication and authorization are separate: where the existing upstream
`system:public-info-viewer` ClusterRoleBinding already grants
`system:unauthenticated` read access to `GET /readyz`, **do not create
another RBAC binding**. Verify the effective binding read-only and check
the following with TLS CA and SAN verification *after* the authorized restart:

- Anonymous HTTPS `GET /readyz` must yield HTTP 200 with body `ok`.
- Anonymous HTTPS `GET /api` and `GET /apis` must be rejected (normally 401).
- An authenticated operator client and all existing staging workloads still
  behave as before; staging node readiness returns to the pre-change baseline.

If this fails, keep the endpoint-scoped gate; do not replace it with globally
enabled anonymous authentication or skip TLS verification. No client key or
token is used for this proposed readiness probe; the CA Secret remains a
**server certificate trust** input on the production Blackbox side.

This change is **staging server only**. It does not touch production k3s,
Kubernetes Secret delivery, the outside-in renderer/admission (currently nine
objects), or Prometheus's planned iSCSI-backed PVC. Those are separately reviewed
stages before O1 apply.

Reference:
- https://kubernetes.io/docs/reference/access-authn-authz/authentication/
- https://github.com/k3s-io/k3s/blob/v1.36.5%2Bk3s1/pkg/daemons/control/server.go

## O1 — minimal deployed components

The renderer is `scripts/prod_outside_in_render.py`.
It creates a Kubernetes List containing:

- A dedicated `observability-prod` namespace.
- One **Blackbox Exporter** Deployment / ClusterIP Service; HTTP `/readyz`
  probes demand TLS verification, status 200, and response body `ok`.
- One small **Prometheus** Deployment / ClusterIP Service, scraping the
  exporter every 30 seconds plus its own health metrics.
- ConfigMaps for the Blackbox modules and Prometheus target list. Each relevant
  Deployment Pod template includes a SHA-256 annotation calculated from the
  exact rendered ConfigMap content. Changing a target recreates the Prometheus
  Pod; changing Blackbox probe modules recreates the Blackbox Pod. Identical
  inputs produce identical annotations and do not cause needless rollouts.
  Automatic Prometheus reload is **not** assumed or enabled.
- Prometheus uses `/-/ready` for readiness (query-serving readiness) and
  `/-/healthy` for liveness. These endpoints intentionally differ.
- Ingress/egress NetworkPolicies scoped to the two Pods and explicitly
  declared narrow destination CIDRs. DNS is allowed on port 53;
  egress probing is confined to TCP 443, 6443 and 9100.
- Prometheus uses a **256Mi emptyDir**, 6-hour retention and 128MB
  retention size; history disappears after Pod rescheduling/recreation.
  This is intentionally acceptable in the first O1 iteration, but it
  is not durable, backup-protected monitoring history. Because configuration
  changes roll the Prometheus Pod, they also discard this ephemeral history.
  If the operator rotates only the *contents* of an existing CA Secret without
  changing its name, the checksum does not change; coordinate an explicit
  Blackbox Pod rollout after such Secret rotation.

The Deployment service accounts do not mount Kubernetes API tokens. Services
use ClusterIP only. No ingress, nodeport, load balancer, API credentials,
staging mutation, collector reconfiguration, alerting, dashboard, or PV is
created. NetworkPolicy enforcement must be independently verified: manifest
existence alone does not prove traffic isolation.

## Site input contract (private; never commit)

Prepare a JSON file outside source control, owned by the operator with mode
0600. This example uses documentation-only domain names and address ranges;
replace all values after the O0 discovery.

```json
{
  "api_targets": [
    {"name": "staging-api", "target": "https://api.lab.example.invalid:6443/readyz"}
  ],
  "tcp_targets": [],
  "egress_cidrs": ["198.51.100.0/24"],
  "api_ca_secret": null
}
```

- `api_targets`: 1–4 approved HTTPS `/readyz` URLs on port 443 or
  6443, no credentials, query strings, or redirects. An API that denies
  unauthenticated requests blocks the current module until a separately
  authorized, **/readyz-only anonymous authentication** configuration is
  confirmed on staging. Never globally open unrelated API endpoints or
  disable TLS verification to bypass this check.
- `tcp_targets`: optional 0–8 `host:9100` targets, only if O0 confirmed
  the host/port is reachable and the node exporter protocol matches.
- `egress_cidrs`: 1–12 IPv4 networks, all with prefix length at least /24.
  Keep them as narrow as the actual observed target IPs allow (prefer /32).
  Targets resolving outside these ranges will fail to probe when the
  NetworkPolicy controller enforces the policy.
- `api_ca_secret`: optional name of a **pre-existing** Kubernetes Secret
  in the namespace, containing key `ca.crt`. Create and own the Secret
  through the private credentials workflow. No CA value is accepted by the
  renderer or stored in the public repo.
- No bearer tokens or credentials are supported in the initial MVP.

The renderer rejects unknown fields, broad CIDRs, unsafe output paths,
HTTP downgrades, unexpected ports, duplicate target names, and missing APIs.
It does not discover or guess staging addresses.

## Render and source validation

After preparing the private site input:

```bash
python3 scripts/prod_outside_in_render.py \
  --site-config /path/to/operator-private/site.json \
  --validate-only

# The following directory must already exist, be operator-owned, and mode 0700.
python3 scripts/prod_outside_in_render.py \
  --site-config /path/to/operator-private/site.json \
  --output /path/to/operator-private/rendered/prod-outside-in.json

# Source/client-side validation only; no cluster changes:
kubectl apply --dry-run=client --validate=false \
  -f /path/to/operator-private/rendered/prod-outside-in.json
```

The output file is created with mode 0600 and must not already exist.
Never send the rendered output to GitHub, a public CI log, or an artifact.
Kubernetes-specific validation, registry image pull, and the apply
controller's policy remain a separate acceptance.

Fixture-only tests (no k3s, network, credentials, or image pull required):

```bash
python3 -m unittest discover -s scripts -p 'test_prod_outside_in_render.py' -v
```

## Operator review / runtime acceptance (not done by this PR)

1. Approve the exact rendered resource set and destinations via the existing
   private production mutation authority, not staging Terraform.
2. Compare prod/stg readiness and the prod datastore mount before/after.
3. Verify only the intended namespace and two Pods are added; verify CPU,
   memory, TSDB space, and DNS/NetworkPolicy behavior.
4. Query Prometheus on the prod side for
   `probe_success{job="staging-api"}`,
   `probe_duration_seconds`, and the self `up` series.
   A real healthy readiness response should produce `probe_success=1`.
   A failed probe should produce `probe_success=0` with an independent
   Prometheus scrape `up=1`. A scrape failure (`up=0`) is different.
5. Use a **test target** to simulate failure. Do not stop/restart the actual
   staging API, k3s, OpenWrt target daemon, or any iSCSI connection.
6. Confirm metrics collection continues within prod when staging probes fail;
   establish loss-of-history behavior across a deliberate **test Pod** restart
   before considering persistent volumes.
7. Only then expand into O2 Grafana, node metrics, persistence, and longer
   retention; O3 alerts and O4 logs follow independently.

## References

- Prometheus Blackbox Exporter configuration:
  https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md
- Prometheus configuration:
  https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Kubernetes NetworkPolicy:
  https://kubernetes.io/docs/concepts/services-networking/network-policies/
