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

  values = [yamlencode({
    metrics = {
      controllerManagerAddr = ":8080"
      listenerAddr          = ":8080"
      listenerEndpoint      = "/metrics"
    }
  })]

  cleanup_on_fail = true
  timeout         = 600
  wait            = true
}


resource "kubernetes_service_v1" "github_actions_runner_controller_metrics" {
  count = var.github_actions_runner_controller_enabled ? 1 : 0

  metadata {
    name      = "arc-controller-metrics"
    namespace = kubernetes_namespace_v1.github_actions_runner_controller[0].metadata[0].name
    annotations = {
      "prometheus.io/scrape" = "true"
      "prometheus.io/path"   = "/metrics"
    }
    labels = {
      "app.kubernetes.io/name"      = "actions-runner-controller"
      "app.kubernetes.io/component" = "metrics"
      "app.kubernetes.io/part-of"   = "ci-stack"
    }
  }

  spec {
    selector = {
      "app.kubernetes.io/part-of"   = "gha-rs-controller"
      "app.kubernetes.io/component" = "controller-manager"
    }

    port {
      name        = "metrics"
      port        = 8080
      target_port = "metrics"
      protocol    = "TCP"
    }
  }

  depends_on = [helm_release.github_actions_runner_controller]
}
