locals {
  labels = {
    "app.kubernetes.io/name"     = var.internal_ca_app_name
    "app.kubernetes.io/instance" = var.internal_ca_cluster_issuer_name
    "app.kubernetes.io/part-of"  = "security-stack"
    environment                  = "shared"
  }
}

resource "kubernetes_secret_v1" "internal_ca" {
  metadata {
    name      = var.internal_ca_secret_name
    namespace = var.cert_manager_namespace
    labels    = local.labels
  }

  data_wo = {
    "tls.crt" = var.internal_ca_tls_crt
    "tls.key" = var.internal_ca_tls_key
  }
  data_wo_revision = var.internal_ca_revision

  type = "kubernetes.io/tls"
}

resource "kubernetes_manifest" "internal_ca_cluster_issuer" {
  manifest = {
    apiVersion = "cert-manager.io/v1"
    kind       = "ClusterIssuer"
    metadata = {
      name   = var.internal_ca_cluster_issuer_name
      labels = local.labels
    }
    spec = {
      ca = {
        secretName = kubernetes_secret_v1.internal_ca.metadata[0].name
      }
    }
  }
}
