locals {
  labels = {
    "app.kubernetes.io/name"       = "cli-workspace"
    "app.kubernetes.io/instance"   = var.name
    "app.kubernetes.io/part-of"    = "cli-tools"
    "app.kubernetes.io/managed-by" = "terraform"
    environment                    = "staging"
  }
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = local.labels
  }
}

# The existing local-path provisioner supplies node affinity after first use.
# Retain prevents PVC removal from deleting Codex state automatically.
resource "kubernetes_storage_class_v1" "state" {
  metadata {
    name = "${var.name}-state-retain"
  }

  storage_provisioner = "rancher.io/local-path"
  reclaim_policy      = "Retain"
  volume_binding_mode = "WaitForFirstConsumer"
}

resource "kubernetes_persistent_volume_claim_v1" "state" {
  # WaitForFirstConsumer cannot bind until the dependent Deployment schedules.
  wait_until_bound = false

  metadata {
    name      = "${var.name}-state"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class_v1.state.metadata[0].name

    resources {
      requests = {
        storage = var.state_storage_size
      }
    }
  }
}

resource "kubernetes_deployment_v1" "this" {
  # The first ARM64 image pull can outlast the provider's 10-minute default.
  timeouts {
    create = "60m"
    update = "60m"
  }

  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    replicas                  = 1
    progress_deadline_seconds = 3600

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = local.labels
    }

    template {
      metadata {
        labels = local.labels
      }

      spec {
        automount_service_account_token = false
        node_selector = {
          "kubernetes.io/hostname" = var.node_name
        }

        container {
          name              = "cli"
          image             = var.image
          image_pull_policy = "IfNotPresent"

          env {
            name  = "USER_NAME"
            value = var.user_name
          }
          env {
            name  = "CLI_USER_ID"
            value = tostring(var.user_id)
          }
          env {
            name  = "CLI_GROUP_ID"
            value = tostring(var.group_id)
          }
          env {
            name  = "CLI_WORKSPACE_MODE"
            value = "1"
          }
          env {
            name  = "CLI_WORKSPACE_TMUX"
            value = var.ssh_enabled ? "1" : "0"
          }
          env {
            name  = "HOME"
            value = "/work-home"
          }
          env {
            name  = "CODEX_HOME"
            value = "/work-state/codex"
          }
          env {
            name  = "PI_CODING_AGENT_DIR"
            value = "/work-state/pi"
          }

          resources {
            requests = {
              cpu                 = "100m"
              memory              = "256Mi"
              "ephemeral-storage" = "512Mi"
            }
            limits = {
              memory              = "2Gi"
              "ephemeral-storage" = "4Gi"
            }
          }

          security_context {
            allow_privilege_escalation = false
          }

          volume_mount {
            name       = "home"
            mount_path = "/work-home"
            sub_path   = var.nas_home_subpath
          }
          volume_mount {
            name       = "state"
            mount_path = "/work-state"
          }
          dynamic "volume_mount" {
            for_each = var.ssh_enabled ? [1] : []
            content {
              name       = "tmux-socket"
              mount_path = "/run/workspace-tmux"
            }
          }
        }

        dynamic "container" {
          for_each = var.ssh_enabled ? [1] : []
          content {
            name              = "ssh"
            image             = var.ssh_image
            image_pull_policy = "IfNotPresent"

            env {
              name  = "USER_NAME"
              value = var.user_name
            }
            env {
              name  = "CLI_USER_ID"
              value = tostring(var.user_id)
            }
            env {
              name  = "CLI_GROUP_ID"
              value = tostring(var.group_id)
            }

            port {
              name           = "ssh"
              container_port = 22
              protocol       = "TCP"
            }

            resources {
              requests = {
                cpu    = "20m"
                memory = "64Mi"
              }
              limits = {
                memory = "256Mi"
              }
            }

            security_context {
              allow_privilege_escalation = false
            }

            volume_mount {
              name       = "state"
              mount_path = "/work-state/ssh"
              sub_path   = "ssh"
            }
            volume_mount {
              name       = "tmux-socket"
              mount_path = "/run/workspace-tmux"
            }
            volume_mount {
              name       = "ssh-authorized-keys"
              mount_path = "/run/workspace-ssh"
              read_only  = true
            }
          }
        }

        volume {
          name = "home"
          nfs {
            server    = var.nas_server
            path      = var.nas_export_path
            read_only = false
          }
        }
        volume {
          name = "state"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.state.metadata[0].name
          }
        }
        dynamic "volume" {
          for_each = var.ssh_enabled ? [1] : []
          content {
            name = "tmux-socket"
            empty_dir {
              medium     = "Memory"
              size_limit = "16Mi"
            }
          }
        }
        dynamic "volume" {
          for_each = var.ssh_enabled ? [1] : []
          content {
            name = "ssh-authorized-keys"
            secret {
              secret_name  = var.ssh_authorized_keys_secret_name
              default_mode = "0444"
              items {
                key  = "authorized_keys"
                path = "authorized_keys"
              }
            }
          }
        }
      }
    }
  }
}

# A standalone Tailscale L3 proxy gives this Service its own MagicDNS name.
resource "kubernetes_service_v1" "ssh" {
  count                  = var.ssh_enabled ? 1 : 0
  wait_for_load_balancer = false

  metadata {
    name      = "${var.name}-ssh"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
    annotations = {
      "tailscale.com/hostname" = var.ssh_tailnet_hostname
      "tailscale.com/tags"     = "tag:cli-workspace-stg"
    }
  }

  spec {
    selector            = local.labels
    type                = "LoadBalancer"
    load_balancer_class = "tailscale"
    port {
      name        = "ssh"
      port        = 22
      target_port = 22
      protocol    = "TCP"
    }
  }
}
