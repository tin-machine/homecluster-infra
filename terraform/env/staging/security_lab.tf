locals {
  security_lab_default_node_selector_annotation_value = join(",", [
    for key in sort(keys(var.workload_node_selector)) : "${key}=${var.workload_node_selector[key]}"
  ])
}

resource "kubernetes_namespace_v1" "security_lab" {
  metadata {
    name = "security-lab"
    labels = {
      "app.kubernetes.io/name"     = "security-lab"
      "app.kubernetes.io/instance" = "security-lab"
      "app.kubernetes.io/part-of"  = "security-monitoring"
      environment                  = "staging"
    }
    annotations = {
      (var.security_lab_default_node_selector_annotation_key) = local.security_lab_default_node_selector_annotation_value
    }
  }
}

resource "kubernetes_resource_quota_v1" "security_lab" {
  metadata {
    name      = "security-lab-quota"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }

  spec {
    hard = {
      pods                         = "8"
      "count/jobs.batch"           = "8"
      persistentvolumeclaims       = "4"
      "requests.cpu"               = "2"
      "requests.memory"            = "4Gi"
      "requests.storage"           = "30Gi"
      "requests.ephemeral-storage" = "8Gi"
      "limits.cpu"                 = "4"
      "limits.memory"              = "8Gi"
      "limits.ephemeral-storage"   = "20Gi"
    }
  }
}

resource "kubernetes_limit_range_v1" "security_lab" {
  metadata {
    name      = "security-lab-defaults"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }

  spec {
    limit {
      type = "Container"

      default = {
        cpu               = "1"
        memory            = "2Gi"
        ephemeral-storage = "4Gi"
      }

      default_request = {
        cpu               = "250m"
        memory            = "512Mi"
        ephemeral-storage = "1Gi"
      }

      max = {
        cpu               = "2"
        memory            = "4Gi"
        ephemeral-storage = "10Gi"
      }

      min = {
        cpu               = "50m"
        memory            = "128Mi"
        ephemeral-storage = "128Mi"
      }
    }

    limit {
      type = "PersistentVolumeClaim"

      max = {
        storage = "30Gi"
      }

      min = {
        storage = "1Gi"
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "security_lab_pcap_input" {
  metadata {
    name      = "security-lab-pcap-input"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
    labels = {
      "app.kubernetes.io/name"      = "security-lab"
      "app.kubernetes.io/instance"  = "security-lab"
      "app.kubernetes.io/part-of"   = "security-monitoring"
      "app.kubernetes.io/component" = "pcap-input"
      environment                   = "staging"
    }
  }

  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = "nfs-client"

    resources {
      requests = {
        storage = "10Gi"
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "security_lab_suricata_output" {
  metadata {
    name      = "security-lab-suricata-output"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
    labels = {
      "app.kubernetes.io/name"      = "security-lab"
      "app.kubernetes.io/instance"  = "security-lab"
      "app.kubernetes.io/part-of"   = "security-monitoring"
      "app.kubernetes.io/component" = "suricata-output"
      environment                   = "staging"
    }
  }

  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = "nfs-client"

    resources {
      requests = {
        storage = "5Gi"
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "security_lab_zeek_output" {
  metadata {
    name      = "security-lab-zeek-output"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
    labels = {
      "app.kubernetes.io/name"      = "security-lab"
      "app.kubernetes.io/instance"  = "security-lab"
      "app.kubernetes.io/part-of"   = "security-monitoring"
      "app.kubernetes.io/component" = "zeek-output"
      environment                   = "staging"
    }
  }

  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = "nfs-client"

    resources {
      requests = {
        storage = "5Gi"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "security_lab_default_deny" {
  metadata {
    name      = "security-lab-default-deny"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }

  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy_v1" "security_lab_allow_dns_and_loki_egress" {
  metadata {
    name      = "security-lab-allow-dns-and-loki-egress"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }

  spec {
    pod_selector {}
    policy_types = ["Egress"]

    egress {
      to {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "kube-system"
          }
        }

        pod_selector {
          match_labels = {
            "k8s-app" = "kube-dns"
          }
        }
      }

      ports {
        protocol = "UDP"
        port     = "53"
      }

      ports {
        protocol = "TCP"
        port     = "53"
      }
    }

    egress {
      to {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "observability-stg"
          }
        }

        pod_selector {
          match_labels = {
            "app.kubernetes.io/instance"  = "stg-loki"
            "app.kubernetes.io/name"      = "loki"
            "app.kubernetes.io/component" = "gateway"
          }
        }
      }

      ports {
        protocol = "TCP"
        port     = "80"
      }
    }
  }
}
