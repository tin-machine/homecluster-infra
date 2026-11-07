mock_provider "kubernetes" {}

variables {
  kubeconfig_path                 = "/unused/mock-kubeconfig"
  image                           = "ghcr.io/example/cli@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ssh_image                       = "ghcr.io/example/ssh@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  node_name                       = "prod-node"
  nas_server                      = "192.0.2.10"
  nas_export_path                 = "/homes"
  nas_home_subpath                = "workspace"
  user_name                       = "workspace"
  user_id                         = 1000
  group_id                        = 1000
  ssh_enabled                     = true
  ssh_tailnet_tags                = ["tag:workspace-prod"]
  ssh_authorized_keys_secret_name = "existing-keys"
  runtime_class_name              = "workspace-overlayfs"
}

run "production_root_wiring" {
  command = plan
  assert {
    condition = (
      output.workspace.environment == "production" && output.workspace.replicas == "1" &&
      output.workspace.node_name == "prod-node" && output.workspace.runtime_class_name == "workspace-overlayfs" &&
      output.workspace.limits.cpu == "2" && output.workspace.limits.memory == "4Gi" &&
      toset(output.workspace.nas_mount_options) == toset(["vers=3", "proto=tcp"]) &&
      output.workspace.tailnet_tags == "tag:workspace-prod"
    )
    error_message = "The production root must forward placement, runtime, limits, NFS options and ACL tags."
  }
}

run "prepare_without_writer_or_endpoint" {
  command = plan
  variables {
    replicas    = 0
    ssh_enabled = false
  }
  assert {
    condition     = output.workspace.replicas == "0" && output.workspace.tailnet_tags == null
    error_message = "Preparation must not run the workspace or reserve its tailnet hostname."
  }
}

run "explicit_nfs_and_retained_state" {
  command = plan
  module {
    source = "../../modules/cli_workspace"
  }
  variables {
    environment       = "production"
    nas_mount_options = ["vers=3", "proto=tcp"]
    cpu_limit         = "1500m"
  }
  assert {
    condition = (
      kubernetes_persistent_volume_v1.home[0].spec[0].persistent_volume_reclaim_policy == "Retain" &&
      toset(kubernetes_persistent_volume_v1.home[0].spec[0].mount_options) == toset(["vers=3", "proto=tcp"]) &&
      kubernetes_persistent_volume_v1.home[0].spec[0].claim_ref[0].name == kubernetes_persistent_volume_claim_v1.home[0].metadata[0].name &&
      kubernetes_persistent_volume_claim_v1.home[0].spec[0].volume_name == kubernetes_persistent_volume_v1.home[0].metadata[0].name &&
      kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[0].persistent_volume_claim[0].claim_name == kubernetes_persistent_volume_claim_v1.home[0].metadata[0].name &&
      length(kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[0].nfs) == 0 &&
      kubernetes_deployment_v1.this.spec[0].template[0].spec[0].container[0].volume_mount[0].sub_path == "workspace" &&
      kubernetes_deployment_v1.this.spec[0].template[0].spec[0].container[0].resources[0].limits.cpu == "1500m"
    )
    error_message = "NFS must bind only the requested export and subpath with explicit mount options and retention."
  }
}

run "module_defaults_unchanged" {
  command = plan
  module {
    source = "../../modules/cli_workspace"
  }
  variables {
    runtime_class_name = null
  }
  assert {
    condition = (
      kubernetes_deployment_v1.this.metadata[0].labels.environment == "staging" &&
      kubernetes_deployment_v1.this.spec[0].replicas == "1" &&
      kubernetes_deployment_v1.this.spec[0].template[0].spec[0].runtime_class_name == null &&
      !contains(keys(kubernetes_deployment_v1.this.spec[0].template[0].spec[0].container[0].resources[0].limits), "cpu") &&
      length(kubernetes_persistent_volume_v1.home) == 0 &&
      kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[0].nfs[0].path == "/homes"
    )
    error_message = "Existing module callers must retain staging, inline NFS, default runtime and no CPU cap."
  }
}

run "reject_environment" {
  command = plan
  variables { environment = "unknown" }
  expect_failures = [var.environment]
}
run "reject_multiple_writers" {
  command = plan
  variables { replicas = 2 }
  expect_failures = [var.replicas]
}
run "reject_empty_acl_tags" {
  command = plan
  variables { ssh_tailnet_tags = [] }
  expect_failures = [var.ssh_tailnet_tags]
}
run "reject_bad_acl_tags" {
  command = plan
  variables { ssh_tailnet_tags = ["*"] }
  expect_failures = [var.ssh_tailnet_tags]
}
run "reject_memory" {
  command = plan
  variables { memory_limit = "0Gi" }
  expect_failures = [var.memory_limit]
}
run "reject_cpu" {
  command = plan
  variables { cpu_limit = "0" }
  expect_failures = [var.cpu_limit]
}
run "reject_runtime_class" {
  command = plan
  variables { runtime_class_name = "bad/runtime" }
  expect_failures = [var.runtime_class_name]
}
