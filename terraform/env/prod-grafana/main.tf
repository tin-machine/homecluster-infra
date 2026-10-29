data "external" "grafana" {
  program = ["python3", "${path.module}/../../../scripts/prod_grafana_terraform_input.py"]
  query = {
    rag_enabled          = tostring(var.rag_enabled)
    folder_uid           = var.existing_folder_uid
    prometheus_site_path = var.prometheus_site_path
    rag_address          = var.rag_enabled ? var.rag_address : ""
    desktop_host_address = var.desktop_host_address
    typevt_host_address  = var.typevt_host_address
    attention_enabled    = tostring(var.attention_enabled)
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
  for_each = { for key, value in local.resources : key => value if value.kind != "Deployment" }
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

resource "kubernetes_manifest" "workload" {
  for_each = { for key, value in local.resources : key => value if value.kind == "Deployment" }
  # Updating the spec also invalidates the computed annotation map so that the
  # controller-owned deployment revision can advance during rollout.
  manifest = merge(each.value, {
    metadata = merge(each.value.metadata, {
      annotations = merge(each.value.metadata.annotations, {
        "homecluster.io/workload-spec-checksum" = sha256(jsonencode(each.value.spec))
      })
    })
  })
  depends_on = [kubernetes_manifest.grafana]

  wait { rollout = true }
  timeouts { update = "5m" }

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


moved {
  from = kubernetes_manifest.grafana["Deployment/prod-grafana"]
  to   = kubernetes_manifest.workload["Deployment/prod-grafana"]
}

moved {
  from = kubernetes_manifest.grafana["Deployment/prod-prometheus"]
  to   = kubernetes_manifest.workload["Deployment/prod-prometheus"]
}
