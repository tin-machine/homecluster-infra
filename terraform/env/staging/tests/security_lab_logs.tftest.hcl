# Plan only, mocked providers, synthetic values; no cluster or credentials.
mock_provider "kubernetes" {}
mock_provider "helm" {}

variables {
  environment                  = "staging"
  release_prefix               = "stg"
  object_storage_namespace     = "object-storage-stg"
  observability_namespace      = "observability-stg"
  tenant_id                    = "staging"
  values_base_dir              = "../../../clusters/homelab/apps/base"
  values_env_dir               = "../../../clusters/homelab/apps/staging"
  values_site_dir              = "../../../clusters/homelab/apps/staging"
  repo_root                    = "../../.."
  grafana_dashboard_files      = []
  grafana_load_balancer_ip     = "192.0.2.10"
  otel_syslog_load_balancer_ip = "192.0.2.11"
  minio_root_user              = "security-fixture"
  minio_root_password          = "security-fixture-not-a-credential"
}

run "delivery_requires_o4" {
  command = plan
  module { source = "../../modules/observability_stack" }
  variables { security_lab_logs_enabled = true }
  expect_failures = [helm_release.otel_collector]
}

run "delivery_appends_after_router_normalizer" {
  command = plan
  module { source = "../../modules/observability_stack" }
  variables {
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
    openwrt_security_logs_enabled  = true
  }
  assert {
    condition     = length(helm_release.otel_collector.values) == 6 && helm_release.otel_collector.values[4] == file("../../../clusters/homelab/apps/staging/values-otel-collector-openwrt-security.yaml") && helm_release.otel_collector.values[5] == file("../../../clusters/homelab/apps/staging/values-otel-collector-security-lab.yaml")
    error_message = "Dedicated delivery must be the final overlay without replacing the router normalizer."
  }
}

run "delivery_without_router_normalizer" {
  command = plan
  module { source = "../../modules/observability_stack" }
  variables {
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
  }
  assert {
    condition     = length(helm_release.otel_collector.values) == 5 && yamldecode(helm_release.otel_collector.values[4]).config.receivers["filelog/security-lab"].start_at == "beginning" && yamldecode(helm_release.otel_collector.values[4]).config.receivers["filelog/security-lab"].storage == "file_storage/security-lab"
    error_message = "security-lab delivery must be independent of the optional router parser."
  }
}
