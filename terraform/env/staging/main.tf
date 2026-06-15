locals {
  repo_root               = abspath("${path.module}/../../..")
  values_base_dir         = "${local.repo_root}/clusters/homelab/apps/base"
  values_env_dir          = "${local.repo_root}/clusters/homelab/apps/staging"
  grafana_dashboard_files = fileset(local.repo_root, "clusters/homelab/apps/base/grafana-dashboards/*.json")
}

module "observability_stack" {
  source = "../../modules/observability_stack"

  environment              = "staging"
  release_prefix           = "stg"
  object_storage_namespace = "object-storage-stg"
  observability_namespace  = "observability-stg"
  tenant_id                = "staging"

  values_base_dir         = local.values_base_dir
  values_env_dir          = local.values_env_dir
  values_site_dir         = var.site_values_dir
  grafana_dashboard_files = local.grafana_dashboard_files
  repo_root               = local.repo_root

  grafana_load_balancer_ip                = var.grafana_load_balancer_ip
  trace_smoke_node_selector               = var.workload_node_selector
  grafana_admin_user                      = var.grafana_admin_user
  grafana_admin_password                  = var.grafana_admin_password
  grafana_admin_secret_revision           = var.grafana_admin_secret_revision
  otel_syslog_load_balancer_ip            = var.otel_syslog_load_balancer_ip
  otel_syslog_load_balancer_source_ranges = var.otel_syslog_load_balancer_source_ranges

  minio_root_user             = var.minio_root_user
  minio_root_password         = var.minio_root_password
  loki_objectstore_access_key = var.loki_objectstore_access_key
  loki_objectstore_secret_key = var.loki_objectstore_secret_key
}

module "homelab_registry" {
  count  = var.registry_enabled ? 1 : 0
  source = "../../modules/homelab_registry"

  environment        = "staging"
  namespace          = var.registry_namespace
  namespace_app_name = var.registry_namespace_app_name
  name               = var.registry_name
  part_of            = var.registry_part_of
  image              = var.registry_image
  replicas           = var.registry_replicas
  node_selector      = var.registry_node_selector
  storage_class_name = var.registry_storage_class_name
  storage_size       = var.registry_storage_size
  load_balancer_ip   = var.registry_load_balancer_ip
  port               = var.registry_port
}
