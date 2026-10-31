# Node labels are reconciled by Terraform on every staged boot. K3s agent
# registration flags alone cannot update an already registered node.
data "kubernetes_nodes" "security_lab" {
  count = var.security_lab_offline_enabled ? 1 : 0
}

data "kubernetes_service_v1" "security_lab_api" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name      = "kubernetes"
    namespace = "default"
  }
}

locals {
  security_lab_api_service_cidr = var.security_lab_offline_enabled ? format("%s/%d",
    data.kubernetes_service_v1.security_lab_api[0].spec[0].cluster_ip,
    strcontains(data.kubernetes_service_v1.security_lab_api[0].spec[0].cluster_ip, ":") ? 128 : 32
  ) : ""
  security_lab_api_node_cidrs = var.security_lab_offline_enabled ? distinct(flatten([
    for node in data.kubernetes_nodes.security_lab[0].nodes : [
      for address in node.status[0].addresses : format("%s/%d", address.address, strcontains(address.address, ":") ? 128 : 32)
      if address.type == "InternalIP" && can(cidrhost(format("%s/%d", address.address, strcontains(address.address, ":") ? 128 : 32), 0))
    ] if anytrue([for key in ["node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master"] : contains(keys(node.metadata[0].labels), key)])
  ])) : []

  security_lab_worker_candidates = var.security_lab_offline_enabled ? [
    for node in data.kubernetes_nodes.security_lab[0].nodes : node
    if node.metadata[0].name == var.security_lab_worker_node
  ] : []
  security_lab_worker = try(local.security_lab_worker_candidates[0], null)
  security_lab_worker_eligible = length(local.security_lab_worker_candidates) == 1 && (
    try(local.security_lab_worker.status[0].node_info[0].architecture, "") == "arm64" &&
    try(local.security_lab_worker.status[0].node_info[0].operating_system, "") == "linux" &&
    try(local.security_lab_worker.spec[0].unschedulable, true) == false &&
    !anytrue([for key in ["node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master"] :
      contains(keys(coalesce(try(local.security_lab_worker.metadata[0].labels, {}), {})), key)
    ]) &&
    length([for taint in coalesce(try(local.security_lab_worker.spec[0].taints, []), []) : taint
      if contains(["NoSchedule", "NoExecute"], taint.effect)
    ]) == 0 &&
    alltrue([for pair in [
      ["Ready", "True"], ["MemoryPressure", "False"], ["DiskPressure", "False"], ["PIDPressure", "False"]
      ] : length([for condition in coalesce(try(local.security_lab_worker.status[0].conditions, []), []) : condition
        if condition.type == pair[0]
        ]) == 1 && length([for condition in coalesce(try(local.security_lab_worker.status[0].conditions, []), []) : condition
        if condition.type == pair[0] && condition.status == pair[1]
    ]) == 1])
  )
}

resource "kubernetes_labels" "security_lab_worker" {
  count         = var.security_lab_offline_enabled ? 1 : 0
  api_version   = "v1"
  kind          = "Node"
  field_manager = "homecluster-security-lab"
  force         = false
  metadata {
    name = var.security_lab_worker_node
  }
  labels = { "security-lab.home.arpa/analysis" = "true" }
  lifecycle {
    precondition {
      condition     = local.security_lab_worker_eligible
      error_message = "Security analysis requires an existing Ready Linux/ARM64 worker without pressure, control-plane labels, scheduling suspension, or NoSchedule/NoExecute taints."
    }
    precondition {
      condition     = length(local.security_lab_api_node_cidrs) > 0 && can(cidrhost(local.security_lab_api_service_cidr, 0))
      error_message = "The coordinator requires the Kubernetes Service IP and observed control-plane InternalIP addresses."
    }
  }
}

resource "kubernetes_service_account_v1" "security_lab_launcher" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name      = "security-lab-launcher"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  automount_service_account_token = false
}

resource "kubernetes_role_v1" "security_lab_launcher" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name      = "security-lab-launcher"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  rule {
    api_groups = ["batch"]
    resources  = ["jobs"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list", "watch"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods/log"]
    verbs      = ["get"]
  }
}

resource "kubernetes_role_binding_v1" "security_lab_launcher" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name      = "security-lab-launcher"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.security_lab_launcher[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.security_lab_launcher[0].metadata[0].name
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
}

resource "kubernetes_cluster_role_v1" "security_lab_node_reader" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name = "security-lab-selected-node-reader"
  }
  rule {
    api_groups     = [""]
    resources      = ["nodes"]
    resource_names = [var.security_lab_worker_node]
    verbs          = ["get"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "security_lab_node_reader" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name = "security-lab-selected-node-reader"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.security_lab_node_reader[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.security_lab_launcher[0].metadata[0].name
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
}

resource "kubernetes_network_policy_v1" "security_lab_coordinator_api" {
  count = var.security_lab_offline_enabled ? 1 : 0
  metadata {
    name      = "security-lab-coordinator-api-only"
    namespace = kubernetes_namespace_v1.security_lab.metadata[0].name
  }
  spec {
    pod_selector {
      match_labels = { "app.kubernetes.io/component" = "security-coordinator" }
    }
    policy_types = ["Egress"]
    egress {
      to {
        ip_block { cidr = local.security_lab_api_service_cidr }
      }
      ports {
        port     = "443"
        protocol = "TCP"
      }
    }
    dynamic "egress" {
      for_each = toset(local.security_lab_api_node_cidrs)
      content {
        to {
          ip_block { cidr = egress.value }
        }
        ports {
          port     = "6443"
          protocol = "TCP"
        }
      }
    }
  }
  depends_on = [kubernetes_labels.security_lab_worker]
}
