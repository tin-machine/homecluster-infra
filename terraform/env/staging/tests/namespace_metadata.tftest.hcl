# Use the real Kubernetes provider: mocking would bypass its validators.
# The CI launcher clears KUBE_* / TF_* inputs and uses an isolated checkout.
provider "kubernetes" {
  host = "http://127.0.0.1:1"
}

mock_provider "helm" {}

variables {
  kubeconfig_path              = "/dev/null"
  minio_root_user              = "namespace-fixture"
  minio_root_password          = "namespace-fixture-not-a-credential"
  grafana_load_balancer_ip     = "192.0.2.10"
  grafana_admin_user           = "namespace-fixture"
  grafana_admin_password       = "namespace-fixture-not-a-credential"
  otel_syslog_load_balancer_ip = "192.0.2.11"
  registry_namespace_app_name  = "registry"
  registry_part_of             = "registry-stack"
  registry_node_selector       = {}
  registry_load_balancer_ip    = "192.0.2.12"
  workload_node_selector       = { "fixture.example.com/role" = "worker" }
  site_values_dir              = "../../../clusters/homelab/apps/staging"
}

run "arc_runner_disabled_security_lab_metadata" {
  command = plan

  plan_options {
    refresh = false
    target  = [
      kubernetes_namespace_v1.github_actions_runners,
      kubernetes_namespace_v1.security_lab,
    ]
  }

  assert {
    condition     = length(kubernetes_namespace_v1.github_actions_runners) == 0
    error_message = "The ARC runner Namespace must remain opt-in."
  }

  assert {
    condition     = kubernetes_namespace_v1.security_lab.metadata[0].name == "security-lab"
    error_message = "The security-lab Namespace name must not change."
  }

  assert {
    condition = kubernetes_namespace_v1.security_lab.metadata[0].labels == tomap({
      "app.kubernetes.io/name"     = "security-lab"
      "app.kubernetes.io/instance" = "security-lab"
      "app.kubernetes.io/part-of"  = "security-monitoring"
      environment                 = "staging"
    })
    error_message = "The real provider must preserve security-lab Namespace label strings."
  }

  assert {
    condition = kubernetes_namespace_v1.security_lab.metadata[0].annotations == tomap({
      "homelab.example.com/default-node-selector" = "fixture.example.com/role=worker"
    })
    error_message = "The generated Namespace annotation must remain a non-null string."
  }
}

run "arc_runner_metadata" {
  command = plan

  variables {
    github_actions_runner_bootstrap_enabled = true
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.github_actions_runners]
  }

  assert {
    condition     = kubernetes_namespace_v1.github_actions_runners[0].metadata[0].name == "arc-runners-stg"
    error_message = "The ARC runner Namespace name must not change during a provider upgrade."
  }

  assert {
    condition = kubernetes_namespace_v1.github_actions_runners[0].metadata[0].labels == tomap({
      "app.kubernetes.io/name"     = "github-actions-runner"
      "app.kubernetes.io/instance" = "homecluster-stg-ci"
      "app.kubernetes.io/part-of"  = "ci-stack"
      environment                 = "staging"
    })
    error_message = "The real provider must preserve all ARC runner Namespace label strings."
  }
}
