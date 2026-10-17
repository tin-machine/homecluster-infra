# Use the real Kubernetes provider: mocking would bypass its validators.
# The CI launcher clears KUBE_* / TF_* inputs and uses an isolated checkout.
provider "kubernetes" {
  host = "http://127.0.0.1:1"
}

mock_provider "helm" {}

variables {
  kubeconfig_path                = "/dev/null"
  metallb_address_pool           = ["192.0.2.64/27"]
  addon_node_selector            = {}
  metallb_tolerate_control_plane = false
}

run "arc_controller_disabled" {
  command = plan

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.github_actions_runner_controller]
  }

  assert {
    condition     = length(kubernetes_namespace_v1.github_actions_runner_controller) == 0
    error_message = "The ARC controller Namespace must remain opt-in."
  }
}

run "arc_controller_metadata" {
  command = plan

  variables {
    github_actions_runner_controller_enabled = true
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.github_actions_runner_controller]
  }

  assert {
    condition     = kubernetes_namespace_v1.github_actions_runner_controller[0].metadata[0].name == "arc-systems"
    error_message = "The ARC controller Namespace name must not change during a provider upgrade."
  }

  assert {
    condition = kubernetes_namespace_v1.github_actions_runner_controller[0].metadata[0].labels == tomap({
      "app.kubernetes.io/name"     = "actions-runner-controller"
      "app.kubernetes.io/instance" = "shared-arc-controller"
      "app.kubernetes.io/part-of"  = "ci-stack"
      environment                 = "shared"
    })
    error_message = "The real provider must preserve all ARC controller Namespace label strings."
  }
}
