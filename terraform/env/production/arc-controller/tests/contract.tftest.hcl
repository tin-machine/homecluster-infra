mock_provider "kubernetes" {}
mock_provider "helm" {}

variables {
  kubeconfig_path        = "/fixture/kubeconfig"
  workload_node_selector = { "kubernetes.io/hostname" = "fixture.invalid" }
}

run "controller_only" {
  command = plan
  assert {
    condition     = helm_release.controller.chart == "gha-runner-scale-set-controller" && helm_release.controller.version == "0.15.0"
    error_message = "Keep the operator chart separate from the runner state."
  }
  assert {
    condition     = yamldecode(helm_release.controller.values[0]).resources.limits.memory == "256Mi"
    error_message = "The operator must have a bounded memory budget."
  }
}
