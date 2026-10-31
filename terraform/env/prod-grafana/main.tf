data "external" "grafana" {
  program = ["python3", "${path.module}/../../../scripts/prod_grafana_terraform_input.py"]
  query = {
    rag_enabled            = tostring(var.rag_enabled)
    folder_uid             = var.existing_folder_uid
    prometheus_site_path   = var.prometheus_site_path
    rag_address            = var.rag_enabled ? var.rag_address : ""
    llm_address            = var.llm_address
    desktop_host_address   = var.desktop_host_address
    typevt_host_address    = var.typevt_host_address
    attention_enabled      = tostring(var.attention_enabled)
    prod_host_enabled      = tostring(var.prod_host_enabled)
    nas_address            = var.nas_address
    nas_username           = var.nas_username
    nas_syslog_enabled     = tostring(var.nas_syslog_enabled)
    security_enabled       = tostring(var.security_enabled)
    security_alerts_paused = tostring(var.security_alerts_paused)
    security_channel_id    = var.security_channel_id
  }
}

data "kubernetes_namespace_v1" "cluster_identity" {
  metadata { name = "kube-system" }
}

locals {
  nas_names = toset([
    "ConfigMap/prod-nas-snmp-config", "Deployment/prod-nas-snmp-exporter",
    "Service/prod-nas-snmp-exporter", "NetworkPolicy/prod-nas-snmp-restricted",
    "NetworkPolicy/prod-nas-prometheus-egress", "ConfigMap/prod-nas-syslog-config",
    "Deployment/prod-nas-syslog", "Service/prod-nas-syslog", "Service/prod-nas-syslog-metrics",
    "NetworkPolicy/prod-nas-syslog-restricted", "NetworkPolicy/prod-nas-loki-ingress"
  ])
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
  for_each = { for key, value in local.resources : key => value if value.kind != "Deployment" && value.kind != "DaemonSet" && !contains(local.nas_names, key) }
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

resource "kubernetes_manifest" "host_exporter" {
  for_each = { for key, value in local.resources : key => value if value.kind == "DaemonSet" }
  manifest = each.value

  field_manager { name = "terraform-prod-grafana" }
  wait {
    fields = {
      "status.desiredNumberScheduled" = "1"
      "status.numberReady"            = "1"
    }
  }
  timeouts {
    create = "5m"
    update = "5m"
    delete = "2m"
  }

  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
    precondition {
      condition     = each.key == "DaemonSet/prod-node-exporter" && each.value.metadata.namespace == "observability-prod"
      error_message = "Only the opt-in production host exporter is allowed."
    }
  }
}

resource "kubernetes_manifest" "workload" {
  for_each = { for key, value in local.resources : key => value if value.kind == "Deployment" && !contains(local.nas_names, key) }
  # Updating the spec also invalidates the computed annotation map so that the
  # controller-owned deployment revision can advance during rollout.
  manifest = merge(each.value, {
    metadata = merge(each.value.metadata, {
      annotations = merge(each.value.metadata.annotations, {
        "homecluster.io/workload-spec-checksum" = sha256(jsonencode(each.value.spec))
      })
    })
  })
  depends_on = [kubernetes_manifest.grafana, kubernetes_manifest.host_exporter, kubernetes_manifest.nas_workload]

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

resource "kubernetes_manifest" "nas_support" {
  for_each = { for key, value in local.resources : key => value if contains(local.nas_names, key) && value.kind != "Deployment" }
  manifest = each.value
  field_manager { name = "terraform-prod-grafana" }
  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
    precondition {
      condition     = contains(local.nas_names, each.key) && each.value.metadata.namespace == "observability-prod"
      error_message = "Only the opt-in NAS monitoring resources are allowed."
    }
  }
}

resource "kubernetes_manifest" "nas_workload" {
  for_each = { for key, value in local.resources : key => value if contains(local.nas_names, key) && value.kind == "Deployment" }
  manifest = merge(each.value, {
    metadata = merge(each.value.metadata, {
      annotations = merge(try(each.value.metadata.annotations, {}), {
        "homecluster.io/workload-spec-checksum" = sha256(jsonencode(each.value.spec))
      })
    })
  })
  depends_on = [kubernetes_manifest.nas_support]
  field_manager { name = "terraform-prod-grafana" }
  wait { rollout = true }
  timeouts {
    create = "5m"
    update = "5m"
  }
  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
    precondition {
      condition     = contains(["Deployment/prod-nas-snmp-exporter", "Deployment/prod-nas-syslog"], each.key) && each.value.metadata.namespace == "observability-prod"
      error_message = "Only the opt-in NAS monitoring workloads are allowed."
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
