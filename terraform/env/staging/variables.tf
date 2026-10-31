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

variable "cli_workspace_enabled" {
  type        = bool
  description = "Opt in to a single staging CLI workspace after image, NAS, and node preflight."
  default     = false
}

variable "cli_workspace_image" {
  type        = string
  description = "Validated ARM64 CLI image pinned by sha256 digest."
  default     = ""
}

variable "cli_workspace_node_name" {
  type        = string
  description = "Staging node holding the retained local state volume."
  default     = ""
}

variable "cli_workspace_nas_server" {
  type        = string
  description = "Site-local NAS NFS server."
  default     = ""
}

variable "cli_workspace_nas_export_path" {
  type        = string
  description = "Existing NAS export root; no new home is provisioned."
  default     = ""
}

variable "cli_workspace_nas_home_subpath" {
  type        = string
  description = "One existing home directory below the NAS export root."
  default     = ""
}

variable "cli_workspace_home_mount_path" {
  type        = string
  description = "Container home path; match existing NAS dotfile symlinks if they use absolute paths."
  default     = "/work-home"
}

variable "cli_workspace_user_name" {
  type        = string
  description = "Unix account name mapped to the existing NAS home."
  default     = ""
}

variable "cli_workspace_user_id" {
  type        = number
  description = "Existing NAS home owner UID."
  default     = 0
}

variable "cli_workspace_group_id" {
  type        = number
  description = "Existing NAS home owner GID."
  default     = 0
}

variable "cli_workspace_ssh_enabled" {
  type        = bool
  description = "Enable the CLI Pod SSH endpoint after the public-key Secret and Tailscale operator are ready."
  default     = false
}

variable "cli_workspace_ssh_image" {
  type        = string
  description = "Validated ARM64 SSH sidecar image pinned by sha256 digest."
  default     = ""
}

variable "cli_workspace_ssh_authorized_keys_secret_name" {
  type        = string
  description = "Name of the precreated cli-tools Secret containing authorized_keys; no key material enters Terraform state."
  default     = "cli-workspace-ssh"
}

variable "cli_workspace_ssh_tailnet_hostname" {
  type        = string
  description = "Dedicated MagicDNS hostname for the workspace SSH Service."
  default     = "workspace"
}

variable "tailscale_operator_enabled" {
  type        = bool
  description = "Install the pinned Tailscale operator only after tailscale/operator-oauth exists."
  default     = false
}

variable "grafana_tailnet_hostname" {
  type        = string
  description = "Optional MagicDNS hostname label for the staging Grafana HTTPS Ingress. Requires the existing Tailscale Operator."
  default     = null
  nullable    = true

  validation {
    condition = var.grafana_tailnet_hostname == null ? true : (
      length(var.grafana_tailnet_hostname) <= 63 &&
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", var.grafana_tailnet_hostname))
    )
    error_message = "grafana_tailnet_hostname must be a single lowercase DNS label of at most 63 characters."
  }
}

variable "typevt_tailnet_fqdn" {
  type        = string
  description = "Optional full MagicDNS name of TypeVT for a staging-only node exporter egress proxy."
  default     = null
  nullable    = true

  validation {
    condition = var.typevt_tailnet_fqdn == null ? true : can(regex(
      "^[a-z0-9-]+(\\.[a-z0-9-]+)+\\.ts\\.net$",
      var.typevt_tailnet_fqdn
    ))
    error_message = "typevt_tailnet_fqdn must be a full lowercase .ts.net MagicDNS name."
  }
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

variable "openwrt_security_logs_enabled" {
  type        = bool
  description = "Opt in to OpenWrt security normalization on the existing O4 production-only log pipeline"
  default     = false
}

variable "o4_prod_container_logs_enabled" {
  type        = bool
  description = "Opt in to the O4 staging Pod log exporter for the production Loki push gateway"
  default     = false
}

variable "registry_port" {
  type        = number
  description = "Registry TCP port"
  default     = 5000
}


variable "github_actions_runner_bootstrap_enabled" {
  type        = bool
  description = "Whether to create the staging ARC runner namespace and CI network boundary before credential bootstrap"
  default     = false
}

variable "github_actions_runner_enabled" {
  type        = bool
  description = "Whether to deploy the staging GitHub Actions runner scale set"
  default     = false
}

variable "github_actions_runner_config_url" {
  type        = string
  sensitive   = true
  description = "Repository or organization URL used to register the staging runner scale set"
  default     = ""
}

variable "github_actions_runner_secret_name" {
  type        = string
  sensitive   = true
  description = "Name of an existing Kubernetes Secret in the runner namespace containing ARC GitHub authentication"
  default     = ""
}

variable "security_lab_offline_enabled" {
  type        = bool
  description = "Opt in to restricted offline security analysis, its dedicated worker label and least-privilege launcher"
  default     = false
}

variable "security_lab_worker_node" {
  type        = string
  description = "Existing staging ARM64 worker selected for security analysis; never a control-plane node"
  default     = ""
}

variable "security_lab_pipeline_enabled" {
  type        = bool
  description = "Enable the single persistent offline-analysis coordinator after boundary and NAS acceptance"
  default     = false
}

variable "security_lab_pipeline_uid" {
  type        = number
  description = "Validated non-root numeric UID/GID used consistently for private NFS artifacts"
  default     = 10001
  validation {
    condition     = var.security_lab_pipeline_uid >= 1000 && var.security_lab_pipeline_uid <= 65534 && floor(var.security_lab_pipeline_uid) == var.security_lab_pipeline_uid
    error_message = "Use an integer non-root UID/GID between 1000 and 65534."
  }
}

variable "security_lab_home_nets" {
  type        = list(string)
  description = "Explicit site HOME_NET CIDRs for offline analysis; no site subnet default"
  default     = []
}
