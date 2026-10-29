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

run "tailnet_is_opt_in" {
  command = plan

  plan_options {
    refresh = false
    target  = [kubernetes_ingress_v1.grafana_tailnet]
  }

  assert {
    condition     = length(kubernetes_ingress_v1.grafana_tailnet) == 0
    error_message = "Grafana tailnet ingress must be absent by default."
  }
}

run "grafana_uses_existing_staging_operator" {
  command = plan

  variables {
    tailscale_operator_enabled = true
    grafana_tailnet_hostname   = "grafana-stg-fixture"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_ingress_v1.grafana_tailnet]
  }

  assert {
    condition = (
      kubernetes_ingress_v1.grafana_tailnet[0].metadata[0].namespace == "observability-stg" &&
      kubernetes_ingress_v1.grafana_tailnet[0].spec[0].ingress_class_name == "tailscale" &&
      kubernetes_ingress_v1.grafana_tailnet[0].spec[0].tls[0].hosts == tolist(["grafana-stg-fixture"]) &&
      kubernetes_ingress_v1.grafana_tailnet[0].spec[0].rule[0].http[0].path[0].backend[0].service[0].name == "stg-grafana" &&
      kubernetes_ingress_v1.grafana_tailnet[0].spec[0].rule[0].http[0].path[0].backend[0].service[0].port[0].number == 80
    )
    error_message = "The HTTPS Ingress must target only the staging Grafana Service."
  }
}

run "operator_is_required" {
  command = plan

  variables {
    tailscale_operator_enabled = false
    grafana_tailnet_hostname   = "grafana-stg-fixture"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_ingress_v1.grafana_tailnet]
  }

  expect_failures = [kubernetes_ingress_v1.grafana_tailnet]
}
