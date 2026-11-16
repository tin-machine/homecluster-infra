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

variable "runner_enabled" {
  type    = bool
  default = false
}

variable "runner_runtime_class_name" {
  description = "Optional pre-existing RuntimeClass. Non-default runtimes require preloaded images."
  type        = string
  default     = null
  validation {
    condition     = var.runner_runtime_class_name == null ? true : can(regex("^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", var.runner_runtime_class_name))
    error_message = "Use a Kubernetes RuntimeClass name or null for the default runtime."
  }
}

variable "github_config_url" {
  type = string
  validation {
    condition     = can(regex("^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_config_url))
    error_message = "Use an explicit repository registration URL."
  }
}

variable "github_config_secret_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+[a-z0-9]$", var.github_config_secret_name))
    error_message = "Use an existing Kubernetes Secret name, never a credential value."
  }
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes = { config_path = var.kubeconfig_path }
}

resource "kubernetes_namespace_v1" "runners" {
  metadata {
    name = "arc-runners-prod"
    labels = {
      "app.kubernetes.io/part-of"                  = "ci-stack"
      "pod-security.kubernetes.io/enforce"         = "restricted"
      "pod-security.kubernetes.io/enforce-version" = "latest"
      environment                                  = "production"
    }
  }
}

resource "kubernetes_service_account_v1" "runner" {
  metadata {
    name      = "ci-runner"
    namespace = kubernetes_namespace_v1.runners.metadata[0].name
  }
  automount_service_account_token = false
}

resource "kubernetes_priority_class_v1" "runner" {
  metadata { name = "arc-ci-background" }
  value             = -10
  global_default    = false
  preemption_policy = "Never"
}

resource "kubernetes_resource_quota_v1" "runner" {
  metadata {
    name      = "ci-runner-budget"
    namespace = kubernetes_namespace_v1.runners.metadata[0].name
  }
  spec {
    hard = {
      pods                         = "1"
      "requests.cpu"               = "500m"
      "limits.cpu"                 = "2"
      "requests.memory"            = "1Gi"
      "limits.memory"              = "2Gi"
      "requests.ephemeral-storage" = "1Gi"
      "limits.ephemeral-storage"   = "2Gi"
    }
  }
}

resource "kubernetes_network_policy_v1" "ci_boundary" {
  metadata {
    name      = "github-actions-runner-ci-boundary"
    namespace = kubernetes_namespace_v1.runners.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
    egress {
      to {
        namespace_selector { match_labels = { "kubernetes.io/metadata.name" = "kube-system" } }
        pod_selector { match_labels = { "k8s-app" = "kube-dns" } }
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
        ip_block {
          cidr = "0.0.0.0/0"
          except = [
            "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
            "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
          ]
        }
      }
      ports {
        protocol = "TCP"
        port     = "443"
      }
    }
  }
}

resource "helm_release" "runners" {
  count      = var.runner_enabled ? 1 : 0
  name       = "homecluster-prod-ci"
  repository = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart      = "gha-runner-scale-set"
  version    = "0.15.0"
  namespace  = kubernetes_namespace_v1.runners.metadata[0].name
  values = [yamlencode({
    githubConfigUrl    = var.github_config_url
    githubConfigSecret = var.github_config_secret_name
    minRunners         = 0
    maxRunners         = 1
    runnerScaleSetName = "homecluster-prod-ci"
    controllerServiceAccount = {
      namespace = "arc-systems"
      name      = "arc-gha-rs-controller"
    }
    listenerTemplate = {
      spec = {
        containers = [{
          name = "listener"
          resources = {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { cpu = "250m", memory = "128Mi" }
          }
        }]
      }
    }
    template = {
      metadata = { labels = { "runner-profile" = "ci-public-egress-only" } }
      spec = merge({
        automountServiceAccountToken = false
        serviceAccountName           = kubernetes_service_account_v1.runner.metadata[0].name
        nodeSelector                 = var.workload_node_selector
        priorityClassName            = kubernetes_priority_class_v1.runner.metadata[0].name
        securityContext = {
          runAsNonRoot   = true
          runAsUser      = 1001
          runAsGroup     = 123
          fsGroup        = 123
          seccompProfile = { type = "RuntimeDefault" }
        }
        containers = [{
          name            = "runner"
          image           = "ghcr.io/actions/actions-runner:2.337.0@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4"
          imagePullPolicy = var.runner_runtime_class_name == null ? "IfNotPresent" : "Never"
          command         = ["/home/runner/run.sh"]
          securityContext = {
            allowPrivilegeEscalation = false
            capabilities             = { drop = ["ALL"] }
          }
          resources = {
            requests = { cpu = "500m", memory = "1Gi", "ephemeral-storage" = "1Gi" }
            limits   = { cpu = "2", memory = "2Gi", "ephemeral-storage" = "2Gi" }
          }
          volumeMounts = [{ name = "work", mountPath = "/home/runner/_work" }]
        }]
        volumes = [{ name = "work", emptyDir = { sizeLimit = "1536Mi" } }]
      }, var.runner_runtime_class_name == null ? {} : { runtimeClassName = var.runner_runtime_class_name })
    }
  })]
  cleanup_on_fail = true
  wait            = true
  timeout         = 600
  depends_on = [
    kubernetes_network_policy_v1.ci_boundary,
    kubernetes_resource_quota_v1.runner,
  ]
}
