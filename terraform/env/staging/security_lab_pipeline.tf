locals {
  security_lab_images = {
    suricata = "jasonish/suricata:8.0.8@sha256:d5a3a1785299ab40918d465e54fedf8e3afeeedba76bb64b0cc74dbc20bda2c3"
    zeek     = "zeek/zeek:9.0.1@sha256:524cb0ce29f26a6a954fb9c357a8f92b95812476d8240f0ece26617ace249dd0"
    helper   = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"
  }
  security_lab_assets = {
    for filename in toset(["runtime.py", "suricata.yaml", "local.rules", "classification.config", "reference.config", "threshold.config"]) : filename =>
    replace(file("${path.module}/security_lab/${filename}"), "@HOME_NET@", "[${join(",", var.security_lab_home_nets)}]")
  }
  security_lab_asset_sha256 = sha256(jsonencode({
    files = local.security_lab_assets, images = local.security_lab_images,
    node  = var.security_lab_worker_node, uid = var.security_lab_pipeline_uid, nfs_version = var.security_lab_nfs_version
  }))
  security_lab_config_name = "security-lab-runtime-${substr(local.security_lab_asset_sha256, 0, 16)}"
}

data "kubernetes_namespace_v1" "security_lab_cluster_identity" {
  count = var.security_lab_pipeline_enabled ? 1 : 0
  metadata { name = "kube-system" }
}

resource "kubernetes_config_map_v1" "security_lab_runtime" {
  count = var.security_lab_pipeline_enabled ? 1 : 0
  metadata {
    name      = local.security_lab_config_name
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  immutable = true
  data = merge(local.security_lab_assets, {
    "settings.json" = jsonencode({
      schema      = 1, node = var.security_lab_worker_node, uid = var.security_lab_pipeline_uid,
      cluster_uid = data.kubernetes_namespace_v1.security_lab_cluster_identity[0].metadata[0].uid,
      config_map  = local.security_lab_config_name, asset_sha256 = local.security_lab_asset_sha256,
      images      = local.security_lab_images, nfs_version = var.security_lab_nfs_version
    })
  })
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.security_lab_offline_enabled && length(var.security_lab_home_nets) > 0 && alltrue([for cidr in var.security_lab_home_nets : can(cidrhost(cidr, 0))])
      error_message = "The offline boundary and explicit valid HOME_NET CIDRs are required before enabling the pipeline."
    }
  }
}

resource "kubernetes_priority_class_v1" "security_lab_background" {
  count = var.security_lab_pipeline_enabled ? 1 : 0
  metadata { name = "security-lab-background" }
  value             = -1000
  global_default    = false
  preemption_policy = "Never"
  description       = "Offline analysis waits for capacity and never preempts existing workloads."
}

resource "kubernetes_deployment_v1" "security_lab_coordinator" {
  count = var.security_lab_pipeline_enabled ? 1 : 0
  metadata {
    name      = "security-lab-coordinator"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  spec {
    replicas = 1
    strategy { type = "Recreate" }
    selector { match_labels = { "app.kubernetes.io/component" = "security-coordinator" } }
    template {
      metadata {
        labels = {
          "app.kubernetes.io/name"      = "pcap-analysis"
          "app.kubernetes.io/component" = "security-coordinator"
        }
      }
      spec {
        service_account_name             = kubernetes_service_account_v1.security_lab_launcher[0].metadata[0].name
        automount_service_account_token  = false
        priority_class_name              = kubernetes_priority_class_v1.security_lab_background[0].metadata[0].name
        termination_grace_period_seconds = 15
        node_selector = {
          "kubernetes.io/hostname"          = var.security_lab_worker_node
          "kubernetes.io/arch"              = "arm64"
          "security-lab.home.arpa/analysis" = "true"
        }
        security_context {
          run_as_non_root = true
          run_as_user     = var.security_lab_pipeline_uid
          run_as_group    = var.security_lab_pipeline_uid
          seccomp_profile { type = "RuntimeDefault" }
        }
        container {
          name    = "coordinator"
          image   = local.security_lab_images.helper
          command = ["python3", "/config/runtime.py", "coordinate"]
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities { drop = ["ALL"] }
          }
          resources {
            requests = { cpu = "50m", memory = "128Mi", "ephemeral-storage" = "128Mi" }
            limits   = { cpu = "250m", memory = "256Mi", "ephemeral-storage" = "128Mi" }
          }
          volume_mount {
            name       = "config"
            mount_path = "/config"
            read_only  = true
          }
          volume_mount {
            name       = "api"
            mount_path = "/var/run/secrets/security-lab"
            read_only  = true
          }
          dynamic "volume_mount" {
            for_each = { input = "input", suricata = "suricata", zeek = "zeek" }
            content {
              name       = volume_mount.key
              mount_path = "/${volume_mount.value}"
            }
          }
        }
        volume {
          name = "config"
          config_map {
            name         = kubernetes_config_map_v1.security_lab_runtime[0].metadata[0].name
            default_mode = "0444"
          }
        }
        volume {
          name = "api"
          projected {
            default_mode = "0444"
            sources {
              service_account_token {
                path               = "token"
                expiration_seconds = 600
              }
            }
            sources {
              config_map {
                name = "kube-root-ca.crt"
                items {
                  key  = "ca.crt"
                  path = "ca.crt"
                }
              }
            }
          }
        }
        dynamic "volume" {
          for_each = {
            input    = kubernetes_persistent_volume_claim_v1.security_lab_pcap_input.metadata[0].name
            suricata = kubernetes_persistent_volume_claim_v1.security_lab_suricata_output.metadata[0].name
            zeek     = kubernetes_persistent_volume_claim_v1.security_lab_zeek_output.metadata[0].name
          }
          content {
            name = volume.key
            persistent_volume_claim { claim_name = volume.value }
          }
        }
      }
    }
  }
  lifecycle {
    precondition {
      condition     = var.security_lab_logs_enabled && var.o4_prod_container_logs_enabled
      error_message = "Enable the dedicated security-lab Collector delivery before the analysis coordinator."
    }
  }
  depends_on = [kubernetes_network_policy_v1.security_lab_coordinator_api, kubernetes_role_binding_v1.security_lab_launcher,
  kubernetes_cluster_role_binding_v1.security_lab_node_reader]
}
