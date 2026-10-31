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

run "default_disabled" {
  command = plan
  module {
    source = "../../modules/observability_stack"
  }
  assert {
    condition     = length(helm_release.otel_collector.values) == 3
    error_message = "Security and O4 overlays must remain opt-in."
  }
}

run "legacy_o4_unchanged" {
  command = plan
  module {
    source = "../../modules/observability_stack"
  }
  variables {
    o4_prod_container_logs_enabled = true
  }
  assert {
    condition     = length(helm_release.otel_collector.values) == 4 && !contains(keys(yamldecode(helm_release.otel_collector.values[3]).config.processors), "transform/openwrt-security")
    error_message = "O4 alone must preserve the existing pipeline without security parsing."
  }
}

run "security_requires_o4" {
  command = plan
  module {
    source = "../../modules/observability_stack"
  }
  variables {
    openwrt_security_logs_enabled = true
  }
  expect_failures = [helm_release.otel_collector]
}

run "explicit_security_overlay_last" {
  command = plan
  module {
    source = "../../modules/observability_stack"
  }
  variables {
    o4_prod_container_logs_enabled = true
    openwrt_security_logs_enabled  = true
  }
  assert {
    condition = length(helm_release.otel_collector.values) == 5 && yamldecode(helm_release.otel_collector.values[4]).config.service.pipelines["logs/openwrt"].processors == [
      "attributes/openwrt", "resource/environment", "resource/openwrt-security", "transform/openwrt-security", "batch/o4-prod-logs"
    ]
    error_message = "The security overlay must load after O4 with the tested processor order."
  }
}
