variable "environment" {
  type        = string
  description = "Environment label value (e.g. prod, staging)"
}

variable "release_prefix" {
  type        = string
  description = "Prefix for release names (e.g. prod, stg)"
}

variable "object_storage_namespace" {
  type        = string
  description = "Namespace for object storage resources"
}

variable "observability_namespace" {
  type        = string
  description = "Namespace for observability resources"
}

variable "tenant_id" {
  type        = string
  description = "Tenant ID for X-Scope-OrgID"
}

variable "values_base_dir" {
  type        = string
  description = "Absolute path to shared values directory"
}

variable "values_env_dir" {
  type        = string
  description = "Absolute path to environment-specific values directory"
}

variable "values_site_dir" {
  type        = string
  description = "Absolute path to generated site-local values directory"
}

variable "grafana_dashboard_files" {
  type        = list(string)
  description = "Dashboard file paths relative to repo root"
}

variable "repo_root" {
  type        = string
  description = "Absolute repository root path"
}

variable "grafana_load_balancer_ip" {
  type        = string
  description = "Static LoadBalancer IP for Grafana service"
}

variable "trace_smoke_node_selector" {
  type        = map(string)
  description = "Optional nodeSelector for the lightweight trace smoke producer. In staging this should follow the OTel Collector DaemonSet placement because the collector Service uses local internal traffic policy."
  default     = {}
}

variable "grafana_admin_user" {
  type        = string
  sensitive   = true
  description = "Optional Grafana admin username. When set with grafana_admin_password, Terraform creates the admin Secret referenced by the Grafana chart."
  default     = null
  nullable    = true
}

variable "grafana_admin_password" {
  type        = string
  sensitive   = true
  description = "Optional Grafana admin password. When set with grafana_admin_user, Terraform creates the admin Secret referenced by the Grafana chart."
  default     = null
  nullable    = true
}

variable "grafana_admin_secret_revision" {
  type        = number
  description = "Revision marker for the write-only Grafana admin Secret data. Increment when rotating the admin credential."
  default     = 1
}

variable "otel_syslog_load_balancer_ip" {
  type        = string
  description = "Static LoadBalancer IP for OTel syslog service"
}

variable "otel_syslog_load_balancer_source_ranges" {
  type        = list(string)
  description = "Optional source CIDRs allowed to reach the OTel syslog LoadBalancer. Leave empty for no Service-level source range restriction."
  default     = []
}

variable "minio_root_user" {
  type        = string
  sensitive   = true
  description = "MinIO root username"
}

variable "minio_root_password" {
  type        = string
  sensitive   = true
  description = "MinIO root password"
}

variable "loki_objectstore_access_key" {
  type        = string
  sensitive   = true
  description = "Access key ID used by Loki to access MinIO"
  default     = null
  nullable    = true
}

variable "loki_objectstore_secret_key" {
  type        = string
  sensitive   = true
  description = "Secret access key used by Loki to access MinIO"
  default     = null
  nullable    = true
}
