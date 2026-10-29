provider "kubernetes" {
  host = "http://127.0.0.1:1"
}

mock_provider "helm" {}

variables {
  kubeconfig_path              = "/dev/null"
  minio_root_user              = "tailnet-fixture"
  minio_root_password          = "tailnet-fixture-not-a-credential"
  grafana_load_balancer_ip     = "192.0.2.10"
  grafana_admin_user           = "tailnet-fixture"
  grafana_admin_password       = "tailnet-fixture-not-a-credential"
  otel_syslog_load_balancer_ip = "192.0.2.11"
  registry_namespace_app_name  = "registry"
  registry_part_of             = "registry-stack"
  registry_node_selector       = {}
  registry_load_balancer_ip    = "192.0.2.12"
  workload_node_selector       = { "fixture.example.com/role" = "worker" }
  site_values_dir              = "../../../clusters/homelab/apps/staging"
}

run "egress_is_opt_in" {
  command = plan

  plan_options {
    refresh = false
    target  = [kubernetes_service_v1.typevt_node_exporter_tailnet]
  }

  assert {
    condition     = length(kubernetes_service_v1.typevt_node_exporter_tailnet) == 0
    error_message = "TypeVT egress must be absent by default."
  }
}

run "egress_targets_only_typevt_metrics" {
  command = plan

  variables {
    tailscale_operator_enabled = true
    typevt_tailnet_fqdn        = "tablet.tail0000.ts.net"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_service_v1.typevt_node_exporter_tailnet]
  }

  assert {
    condition = (
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].metadata[0].namespace == "observability-stg" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].metadata[0].annotations["tailscale.com/tailnet-fqdn"] == "tablet.tail0000.ts.net" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].spec[0].type == "ExternalName" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].spec[0].port[0].port == 9100
    )
    error_message = "The egress Service must target only TypeVT's tailnet metrics endpoint."
  }
}

run "operator_is_required_for_egress" {
  command = plan

  variables {
    tailscale_operator_enabled = false
    typevt_tailnet_fqdn        = "tablet.tail0000.ts.net"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_service_v1.typevt_node_exporter_tailnet]
  }

  expect_failures = [kubernetes_service_v1.typevt_node_exporter_tailnet]
}
