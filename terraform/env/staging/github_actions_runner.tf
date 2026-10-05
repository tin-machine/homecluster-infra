resource "kubernetes_namespace_v1" "github_actions_runners" {
  count = var.github_actions_runner_enabled ? 1 : 0

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
    template = {
      spec = {
        nodeSelector = var.workload_node_selector
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

  lifecycle {
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
