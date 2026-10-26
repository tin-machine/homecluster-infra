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
    condition     = kubernetes_deployment_v1.this.spec[0].progress_deadline_seconds == 3600
    error_message = "The first ARM64 image pull needs a one-hour rollout deadline."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.timeouts.create == "60m" && kubernetes_deployment_v1.this.timeouts.update == "60m"
    error_message = "Terraform must wait as long as Kubernetes for the workspace rollout."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].node_selector["kubernetes.io/hostname"] == "staging-node"
    error_message = "The retained local state must stay on the selected node."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[0].nfs[0].path == "/homes"
    error_message = "The existing NAS export must be mounted directly."
  }

  assert {
    condition     = length(kubernetes_service_v1.ssh) == 0
    error_message = "SSH must remain opt-in until credentials are provisioned."
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

run "ssh_endpoint_is_explicit_and_tailnet_only" {
  command = plan

  module {
    source = "../../modules/cli_workspace"
  }

  plan_options {
    refresh = false
  }

  variables {
    ssh_enabled                     = true
    ssh_authorized_keys_secret_name = "workspace-keys"
    ssh_tailnet_hostname            = "workspace-test"
  }

  assert {
    condition     = kubernetes_service_v1.ssh[0].spec[0].load_balancer_class == "tailscale"
    error_message = "SSH must only be advertised through the Tailscale load balancer class."
  }

  assert {
    condition     = kubernetes_service_v1.ssh[0].metadata[0].annotations["tailscale.com/hostname"] == "workspace-test"
    error_message = "SSH must use a dedicated MagicDNS hostname."
  }

  assert {
    condition     = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].volume[2].secret[0].secret_name == "workspace-keys"
    error_message = "Only a precreated authorized_keys Secret name may enter the Pod plan."
  }
}
