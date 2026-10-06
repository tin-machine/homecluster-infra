locals {
  # ARC creates AutoscalingListener resources and listener Pods in the
  # controller namespace, not in the runner scale-set namespace.
  github_actions_runner_controller_namespace = "arc-systems"
}

resource "kubernetes_namespace_v1" "github_actions_runners" {
  count = var.github_actions_runner_bootstrap_enabled ? 1 : 0

  metadata {
    name = "arc-runners-stg"
    labels = {
      "app.kubernetes.io/name"     = "github-actions-runner"
      "app.kubernetes.io/instance" = "homecluster-stg-ci"
      "app.kubernetes.io/part-of"  = "ci-stack"
      environment                  = "staging"
    }
  }
}

resource "kubernetes_network_policy_v1" "github_actions_runner_ci_boundary" {
  count = var.github_actions_runner_bootstrap_enabled ? 1 : 0

  metadata {
    name      = "github-actions-runner-ci-boundary"
    namespace = kubernetes_namespace_v1.github_actions_runners[0].metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        "runner-profile" = "ci-public-egress-only"
      }
    }

    policy_types = ["Ingress", "Egress"]

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

    # The CI scale set may reach public HTTPS endpoints required by GitHub,
    # package registries, and source dependencies. Private/link-local ranges
    # stay unreachable. A future LAN-mutating runner must use a separate scale
    # set and an explicitly reviewed network policy instead of widening this one.
    egress {
      to {
        ip_block {
          cidr = "0.0.0.0/0"
          except = [
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.168.0.0/16",
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

resource "helm_release" "github_actions_runner" {
  count = var.github_actions_runner_enabled ? 1 : 0

  name       = "homecluster-stg-ci"
  repository = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart      = "gha-runner-scale-set"
  namespace  = kubernetes_namespace_v1.github_actions_runners[0].metadata[0].name
  version    = "0.15.0"

  values = [yamlencode({
    minRunners         = 0
    maxRunners         = 2
    runnerScaleSetName = "homecluster-stg-ci"
    resourceMeta = {
      autoscalingListener = {
        labels = {
          "homelab.example.com/metrics-target" = "arc-stg-ci"
        }
      }
    }
    template = {
      metadata = {
        labels = {
          "runner-profile" = "ci-public-egress-only"
        }
      }
      spec = {
        automountServiceAccountToken = false
        nodeSelector                 = var.workload_node_selector
        containers = [
          {
            name    = "runner"
            image   = "ghcr.io/actions/actions-runner:2.337.0"
            command = ["/home/runner/run.sh"]
          }
        ]
      }
    }
  })]

  set_sensitive {
    name  = "githubConfigUrl"
    value = var.github_actions_runner_config_url
  }

  set_sensitive {
    name  = "githubConfigSecret"
    value = var.github_actions_runner_secret_name
  }

  cleanup_on_fail = true
  timeout         = 600
  wait            = true

  depends_on = [
    kubernetes_network_policy_v1.github_actions_runner_ci_boundary,
  ]

  lifecycle {
    precondition {
      condition     = var.github_actions_runner_bootstrap_enabled
      error_message = "github_actions_runner_bootstrap_enabled must be true before github_actions_runner_enabled can be enabled."
    }

    precondition {
      condition     = try(length(trimspace(var.github_actions_runner_config_url)) > 0, false)
      error_message = "github_actions_runner_config_url must be set when github_actions_runner_enabled is true."
    }

    precondition {
      condition     = try(length(trimspace(var.github_actions_runner_secret_name)) > 0, false)
      error_message = "github_actions_runner_secret_name must be set when github_actions_runner_enabled is true."
    }
  }
}

resource "kubernetes_service_v1" "github_actions_runner_listener_metrics" {
  count = var.github_actions_runner_enabled ? 1 : 0

  metadata {
    name      = "homecluster-stg-ci-listener-metrics"
    namespace = local.github_actions_runner_controller_namespace
    annotations = {
      "prometheus.io/scrape" = "true"
      "prometheus.io/path"   = "/metrics"
    }
    labels = {
      "app.kubernetes.io/name"      = "github-actions-runner"
      "app.kubernetes.io/component" = "metrics"
      "app.kubernetes.io/part-of"   = "ci-stack"
      environment                   = "staging"
    }
  }

  spec {
    selector = {
      "app.kubernetes.io/component"        = "runner-scale-set-listener"
      "homelab.example.com/metrics-target" = "arc-stg-ci"
    }

    port {
      name        = "metrics"
      port        = 8080
      target_port = 8080
      protocol    = "TCP"
    }
  }

  depends_on = [helm_release.github_actions_runner]
}
