terraform {
  required_version = ">= 1.7.0"
  backend "local" {}
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
  }
}

variable "kubeconfig_path" {
  type = string
}

variable "workload_node_selector" {
  type = map(string)
  validation {
    condition     = length(var.workload_node_selector) > 0
    error_message = "An explicit node selector is required."
  }
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes = { config_path = var.kubeconfig_path }
}

resource "kubernetes_namespace_v1" "controller" {
  metadata {
    name = "arc-systems"
    labels = {
      "app.kubernetes.io/part-of" = "ci-stack"
      environment                 = "production"
    }
  }
}

resource "helm_release" "controller" {
  name       = "arc"
  repository = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart      = "gha-runner-scale-set-controller"
  version    = "0.15.0"
  namespace  = kubernetes_namespace_v1.controller.metadata[0].name
  values = [yamlencode({
    nodeSelector = var.workload_node_selector
    resources = {
      requests = { cpu = "100m", memory = "128Mi" }
      limits   = { cpu = "500m", memory = "256Mi" }
    }
    metrics = {
      controllerManagerAddr = ":8080"
      listenerAddr          = ":8080"
      listenerEndpoint      = "/metrics"
    }
  })]
  cleanup_on_fail = true
  wait            = true
  timeout         = 600
}
