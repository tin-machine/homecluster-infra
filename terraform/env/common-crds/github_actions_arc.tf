resource "kubernetes_namespace_v1" "github_actions_runner_controller" {
  count = var.github_actions_runner_controller_enabled ? 1 : 0

  metadata {
    name = "arc-systems"
    labels = {
      "app.kubernetes.io/name"     = "actions-runner-controller"
      "app.kubernetes.io/instance" = "shared-arc-controller"
      "app.kubernetes.io/part-of"  = "ci-stack"
      environment                  = "shared"
    }
  }
}

resource "helm_release" "github_actions_runner_controller" {
  count = var.github_actions_runner_controller_enabled ? 1 : 0

  name       = "arc"
  repository = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart      = "gha-runner-scale-set-controller"
  namespace  = kubernetes_namespace_v1.github_actions_runner_controller[0].metadata[0].name
  version    = "0.15.0"

  cleanup_on_fail = true
  timeout         = 600
  wait            = true
}
