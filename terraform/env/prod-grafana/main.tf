data "external" "grafana" {
  program = ["python3", "${path.module}/../../../scripts/prod_grafana_terraform_input.py"]
  query = {
    rag_enabled          = tostring(var.rag_enabled)
    folder_uid           = var.existing_folder_uid
    prometheus_site_path = var.prometheus_site_path
    rag_address          = var.rag_enabled ? var.rag_address : ""
  }
}

data "kubernetes_namespace_v1" "cluster_identity" {
  metadata { name = "kube-system" }
}

locals {
  rendered = jsondecode(data.external.grafana.result.manifest)
  resources = {
    for resource in local.rendered.items :
    "${resource.kind}/${resource.metadata.name}" => merge(resource, {
      metadata = merge(resource.metadata, var.mark_terraform_ownership ? {
        annotations = merge(try(resource.metadata.annotations, {}), {
          "homecluster.io/configuration-owner" = "terraform-prod-grafana"
        })
      } : {})
    })
  }
}

resource "kubernetes_manifest" "grafana" {
  for_each = local.resources
  manifest = each.value

  field_manager {
    name            = "terraform-prod-grafana"
    force_conflicts = var.adopt_existing_field_ownership
  }

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
    precondition {
      condition = each.value.metadata.namespace == "observability-prod" && contains([
        "ConfigMap/prod-grafana-provisioning", "Deployment/prod-grafana", "Service/prod-grafana",
        "NetworkPolicy/prod-grafana-restricted", "NetworkPolicy/prod-prometheus-grafana-ingress",
        "ConfigMap/prod-prometheus-config", "Deployment/prod-prometheus", "NetworkPolicy/prod-prometheus-restricted"
      ], each.key)
      error_message = "Only the existing production Grafana resources are allowed."
    }
  }
}
