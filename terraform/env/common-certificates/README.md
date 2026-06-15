# common-certificates

Shared certificate issuers for the staging k3s cluster.

This state must be applied after `terraform/env/common-crds`, because it uses cert-manager CRDs through `kubernetes_manifest`.

It creates:

- Secret `cert-manager/homelab-internal-ca`
- ClusterIssuer `homelab-internal-ca`

The CA certificate and private key are runtime secrets. Keep them outside the repository, for example in a root-only tfvars file:

```hcl
internal_ca_tls_crt = "<PEM certificate>"
internal_ca_tls_key = "<PEM private key>"
internal_ca_revision = 1
```

Apply with a site-local backend and a site-local var file:

```bash
terraform -chdir=terraform/env/common-certificates init \
  -backend-config='path=/srv/terraform-state/staging/common-certificates.tfstate'

terraform -chdir=terraform/env/common-certificates apply \
  -var='kubeconfig_path=/etc/rancher/k3s/k3s.yaml' \
  -var-file=/srv/terraform-state/staging/internal-ca.tfvars
```

The Secret uses the Kubernetes provider write-only `data_wo` attribute. Terraform state tracks the revision marker, not the CA private key itself. Increment `internal_ca_revision` when rotating the CA.

Do not commit the CA private key, generated tfvars, kubeconfig, or Terraform state.
