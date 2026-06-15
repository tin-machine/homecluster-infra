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
  description = "Access key ID used by Loki to access MinIO (defaults to minio_root_user when null)"
  default     = null
  nullable    = true
}

variable "loki_objectstore_secret_key" {
  type        = string
  sensitive   = true
  description = "Secret access key used by Loki to access MinIO (defaults to minio_root_password when null)"
  default     = null
  nullable    = true
}

variable "grafana_load_balancer_ip" {
  type        = string
  description = "Static LoadBalancer IP for staging Grafana service"
}

variable "grafana_admin_user" {
  type        = string
  sensitive   = true
  description = "Grafana admin username for staging"
}

variable "grafana_admin_password" {
  type        = string
  sensitive   = true
  description = "Grafana admin password for staging"
}

variable "grafana_admin_secret_revision" {
  type        = number
  description = "Revision marker for the write-only Grafana admin Secret data. Increment when rotating the admin credential."
  default     = 1
}

variable "otel_syslog_load_balancer_ip" {
  type        = string
  description = "Static LoadBalancer IP for staging OTel syslog service"
}

variable "otel_syslog_load_balancer_source_ranges" {
  type        = list(string)
  description = "Optional source CIDRs allowed to reach the staging OTel syslog LoadBalancer"
  default     = []
}

variable "registry_enabled" {
  type        = bool
  description = "Whether to deploy the staging homelab registry. Enable only via external site input."
  default     = false
}

variable "registry_namespace" {
  type        = string
  description = "Namespace for staging local registry resources"
  default     = "registry-stg"
}

variable "registry_namespace_app_name" {
  type        = string
  description = "app.kubernetes.io/name label for the registry namespace"
}

variable "registry_name" {
  type        = string
  description = "Name used for the homelab registry Deployment, Service, and PVC"
  default     = "homelab-registry"
}

variable "registry_part_of" {
  type        = string
  description = "app.kubernetes.io/part-of label for registry resources"
}

variable "registry_image" {
  type        = string
  description = "Registry image"
  default     = "registry:2"
}

variable "registry_replicas" {
  type        = number
  description = "Number of registry replicas"
  default     = 1
}

variable "registry_node_selector" {
  type        = map(string)
  description = "Node selector for registry workload placement"
}

variable "registry_storage_class_name" {
  type        = string
  description = "StorageClass for registry blob storage"
  default     = "nfs-client"
}

variable "registry_storage_size" {
  type        = string
  description = "PVC size for registry blob storage"
  default     = "20Gi"
}

variable "registry_load_balancer_ip" {
  type        = string
  description = "Static MetalLB BGP VIP for the registry LoadBalancer service"
}

variable "workload_node_selector" {
  type        = map(string)
  description = "Node selector for staging observability workloads"
}

variable "security_lab_default_node_selector_annotation_key" {
  type        = string
  description = "Annotation key used to document the intended default node selector for security-lab"
  default     = "homelab.example.com/default-node-selector"
}

variable "site_values_dir" {
  type        = string
  description = "Absolute path to generated site-local Helm values. Every required chart file must exist."
}

variable "registry_port" {
  type        = number
  description = "Registry TCP port"
  default     = 5000
}
