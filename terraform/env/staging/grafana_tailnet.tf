# This uses the pre-existing staging Operator and its Secret. No OAuth value enters Terraform.
resource "kubernetes_ingress_v1" "grafana_tailnet" {
  count = var.grafana_tailnet_hostname == null ? 0 : 1

  metadata {
    name      = "stg-grafana-tailnet"
    namespace = "observability-stg"
    annotations = {
      # The staging OAuth client can issue proxy keys for this existing tag.
      "tailscale.com/tags" = "tag:cli-workspace-stg"
    }
    labels = {
      "app.kubernetes.io/name"     = "grafana"
      "app.kubernetes.io/instance" = "stg-grafana"
      "app.kubernetes.io/part-of"  = "observability"
      environment                  = "staging"
    }
  }

  spec {
    ingress_class_name = "tailscale"

    tls {
      hosts = [var.grafana_tailnet_hostname]
    }

    rule {
      http {
        path {
          path      = "/"
          path_type = "Prefix"
          backend {
            service {
              name = "stg-grafana"
              port { number = 80 }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.tailscale_operator, module.observability_stack]

  lifecycle {
    precondition {
      condition     = var.tailscale_operator_enabled
      error_message = "The existing staging Tailscale Operator must be enabled before Grafana tailnet ingress."
    }
  }
}
