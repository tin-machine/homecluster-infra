# The Operator changes externalName to its managed headless Service.
# The source hostname is supplied by private site input, never public code.
resource "kubernetes_service_v1" "typevt_node_exporter_tailnet" {
  count = var.typevt_tailnet_fqdn == null ? 0 : 1

  metadata {
    name      = "typevt-node-exporter-tailnet"
    namespace = "observability-stg"
    annotations = {
      "tailscale.com/tailnet-fqdn" = var.typevt_tailnet_fqdn
      "tailscale.com/tags"         = "tag:cli-workspace-stg"
    }
    labels = {
      "app.kubernetes.io/name"    = "node-exporter"
      "app.kubernetes.io/part-of" = "observability"
      environment                 = "staging"
    }
  }

  spec {
    type          = "ExternalName"
    external_name = "placeholder"
    port {
      name     = "metrics"
      port     = 9100
      protocol = "TCP"
    }
  }

  depends_on = [helm_release.tailscale_operator]

  lifecycle {
    ignore_changes = [spec[0].external_name]

    precondition {
      condition     = var.tailscale_operator_enabled
      error_message = "The existing staging Tailscale Operator must be enabled before TypeVT egress."
    }
  }
}
