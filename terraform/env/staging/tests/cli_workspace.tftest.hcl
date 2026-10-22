provider "kubernetes" {
  host = "http://127.0.0.1:1"
}

mock_provider "helm" {}

variables {
  image              = "ghcr.io/example/cli-tool@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  node_name          = "staging-node"
  nas_server         = "192.0.2.10"
  nas_export_path    = "/homes"
  nas_home_subpath   = "workspace"
  user_name          = "workspace"
  user_id            = 503
  group_id           = 1000
  state_storage_size = "10Gi"
}

run "workspace_storage_and_identity" {
  command = plan

  module {
    source = "../../modules/cli_workspace"
  }

  plan_options {
    refresh = false
  }

  assert {
    condition     = kubernetes_storage_class_v1.state.reclaim_policy == "Retain"
    error_message = "Workspace state must retain its PV after PVC removal."
  }

  assert {
    condition     = kubernetes_storage_class_v1.state.volume_binding_mode == "WaitForFirstConsumer"
    error_message = "Workspace state must bind to the selected node."
  }

  assert {
    condition     = kubernetes_persistent_volume_claim_v1.state.wait_until_bound == false
    error_message = "The PVC must not wait for binding before its consumer Deployment exists."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].strategy[0].type == "Recreate"
    error_message = "A shared NAS home must not be used by two rollout replicas."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].node_selector["kubernetes.io/hostname"] == "staging-node"
    error_message = "The retained local state must stay on the selected node."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[0].nfs[0].path == "/homes"
    error_message = "The existing NAS export must be mounted directly."
  }
}

run "reject_unpinned_image" {
  command = plan

  module {
    source = "../../modules/cli_workspace"
  }

  plan_options {
    refresh = false
  }

  variables {
    image = "ghcr.io/example/cli-tool:latest"
  }

  expect_failures = [var.image]
}
