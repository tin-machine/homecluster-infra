# Reuse the production Operator; do not provision credentials or a second release.
resource "kubernetes_service_v1" "typevt_node_exporter_tailnet" {
  count = var.typevt_tailnet_fqdn == "" ? 0 : 1

  metadata {
    name      = "typevt-node-exporter-tailnet"
    namespace = "observability-prod"
    annotations = {
      "tailscale.com/tailnet-fqdn"         = var.typevt_tailnet_fqdn
      "homecluster.io/configuration-owner" = "terraform-prod-grafana"
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

  lifecycle {
    # The Operator replaces this with its own headless Service name.
    ignore_changes = [spec[0].external_name]
    precondition {
      condition     = var.typevt_host_address == "" && var.prometheus_site_path != ""
      error_message = "TypeVT egress requires the production Prometheus site and no direct TypeVT address."
    }
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
  }
}
