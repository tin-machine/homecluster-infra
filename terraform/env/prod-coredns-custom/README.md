# Prod CoreDNS Custom ConfigMap

## Purpose

This independent Terraform root manages only one object in the production
cluster: the `coredns-custom` ConfigMap in `kube-system`. It does not
manage the CoreDNS Deployment or the standard Corefile, and it does not change
DNS behavior.

## Scope

Managed object: `kubernetes_config_map_v1.custom`

- Namespace: `kube-system`
- Name: `coredns-custom`
- Data keys: `empty.override` and `empty.server`
- Values: comments only, so CoreDNS keeps its default resolution behavior

```yaml
empty.override: |
  # No custom DNS overrides.
empty.server: |
  # No custom DNS servers.
```

## Identity gate

Before applying, the namespace UID check must match the previously verified
production `kube-system` UID. The precondition is:

```hcl
data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
```

If the UID does not match, Terraform plan/apply stops with a clear error.
`prevent_destroy = true` is set so an accidental deletion of the existing
ConfigMap is not possible through Terraform.

## Existing ConfigMap

If `coredns-custom` already exists, stop before applying. A new state does not
adopt existing objects automatically. Review its ownership and contents before
considering a separate import; do not overwrite custom DNS configuration.

## State

Use a dedicated private local backend. The local state, tfvars, kubeconfig,
and plans must remain outside this repository.

```bash
terraform init -backend-config="path=/private/state/prod-coredns-custom.tfstate"
terraform plan -var-file=/private/site/prod-coredns-custom.tfvars
terraform apply -var-file=/private/site/prod-coredns-custom.tfvars
```

## Rollback

Restore the previous source revision and review the plan before applying.
Do not use `terraform destroy` to remove the ConfigMap. Keep a state backup
and the previous revision for recovery.

## What this root does not do

- Does not manage `kube-system/coredns` Deployment or Service.
- Does not modify the standard Corefile or CoreDNS image.
- Does not create custom DNS records.
- Does not change CoreDNS server, upstream, or cache settings.
- Does not store credentials, kubeconfig, or live cluster addresses in Git.
