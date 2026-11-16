mock_provider "kubernetes" {}
mock_provider "helm" {}

variables {
  kubeconfig_path           = "/fixture/kubeconfig"
  workload_node_selector    = { "kubernetes.io/hostname" = "fixture.invalid" }
  github_config_url         = "https://github.com/fixture/private-ci"
  github_config_secret_name = "fixture-github-auth"
}

run "bootstrap_without_registration" {
  command = plan
  assert {
    condition     = length(helm_release.runners) == 0
    error_message = "The default must create boundaries without registering a runner."
  }
  assert {
    condition     = kubernetes_service_account_v1.runner.automount_service_account_token == false
    error_message = "Runner ServiceAccounts must not receive an API token."
  }
}

run "ci_only_registration" {
  command = plan
  variables { runner_enabled = true }
  assert {
    condition = (
      yamldecode(helm_release.runners[0].values[0]).minRunners == 0 &&
      yamldecode(helm_release.runners[0].values[0]).maxRunners == 1 &&
      yamldecode(helm_release.runners[0].values[0]).githubConfigSecret == "fixture-github-auth"
    )
    error_message = "Keep one ephemeral runner and a Secret reference."
  }
  assert {
    condition = (
      yamldecode(helm_release.runners[0].values[0]).template.spec.automountServiceAccountToken == false &&
      yamldecode(helm_release.runners[0].values[0]).template.spec.containers[0].resources.limits["ephemeral-storage"] == "2Gi" &&
      yamldecode(helm_release.runners[0].values[0]).template.spec.containers[0].securityContext.allowPrivilegeEscalation == false &&
      yamldecode(helm_release.runners[0].values[0]).template.spec.volumes[0].emptyDir.sizeLimit == "1536Mi"
    )
    error_message = "Keep the CI storage and privilege boundary when enabling registration."
  }
}
