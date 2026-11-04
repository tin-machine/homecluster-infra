data "kubernetes_namespace_v1" "cluster_identity" {
  metadata { name = "kube-system" }
}

resource "kubernetes_config_map_v1" "custom" {
  metadata {
    namespace = data.kubernetes_namespace_v1.cluster_identity.metadata[0].name
    name      = "coredns-custom"
    annotations = {
      "homecluster.io/configuration-owner" = "terraform-prod-coredns-custom"
    }
  }

  data = {
    "empty.override" = "# No custom DNS overrides.\n"
    "empty.server"   = "# No custom DNS servers.\n"
  }

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
  }
}
