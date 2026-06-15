locals {
  namespace_labels = {
    "app.kubernetes.io/name"     = var.namespace_app_name
    "app.kubernetes.io/instance" = var.namespace
    "app.kubernetes.io/part-of"  = var.part_of
    environment                  = var.environment
  }

  labels = {
    "app.kubernetes.io/name"     = var.name
    "app.kubernetes.io/instance" = var.name
    "app.kubernetes.io/part-of"  = var.part_of
    environment                  = var.environment
  }
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = local.namespace_labels
  }
}

resource "kubernetes_persistent_volume_claim_v1" "this" {
  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name

    resources {
      requests = {
        storage = var.storage_size
      }
    }
  }
}

resource "kubernetes_deployment_v1" "this" {
  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = local.labels
    }

    template {
      metadata {
        labels = local.labels
      }

      spec {
        node_selector = var.node_selector

        security_context {
          fs_group = 1000
        }

        container {
          name              = "registry"
          image             = var.image
          image_pull_policy = "IfNotPresent"

          port {
            name           = "registry"
            container_port = var.port
          }

          env {
            name  = "REGISTRY_STORAGE_DELETE_ENABLED"
            value = "true"
          }

          resources {
            requests = {
              cpu                 = "100m"
              memory              = "256Mi"
              "ephemeral-storage" = "256Mi"
            }
            limits = {
              memory              = "1Gi"
              "ephemeral-storage" = "1Gi"
            }
          }

          security_context {
            allow_privilege_escalation = false

            capabilities {
              drop = ["ALL"]
            }
          }

          readiness_probe {
            http_get {
              path = "/v2/"
              port = "registry"
            }
            initial_delay_seconds = 5
            period_seconds        = 10
          }

          liveness_probe {
            http_get {
              path = "/v2/"
              port = "registry"
            }
            initial_delay_seconds = 30
            period_seconds        = 30
          }

          volume_mount {
            name       = "registry-data"
            mount_path = "/var/lib/registry"
          }
        }

        volume {
          name = "registry-data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.this.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "this" {
  metadata {
    name      = var.name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
    annotations = {
      "metallb.universe.tf/address-pool" = "vip-bgp-pool"
    }
  }

  spec {
    type             = "LoadBalancer"
    load_balancer_ip = var.load_balancer_ip

    selector = local.labels

    port {
      name        = "registry"
      port        = var.port
      target_port = "registry"
      protocol    = "TCP"
    }
  }

  lifecycle {
    ignore_changes = [
      metadata[0].annotations["metallb.universe.tf/ip-allocated-from-pool"],
    ]
  }

  depends_on = [kubernetes_deployment_v1.this]
}
