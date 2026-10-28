variable "kubeconfig_path" {
  description = "Private production kubeconfig outside the source tree."
  type        = string
}

variable "kubeconfig_context" {
  description = "Explicit production context."
  type        = string
}

variable "expected_kube_system_uid" {
  description = "Previously verified production cluster identity."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f-]{36}$", var.expected_kube_system_uid))
    error_message = "An expected kube-system UID is required."
  }
}

variable "mark_terraform_ownership" {
  description = "Enable after import, unchanged-object baseline review and write-ownership transfer."
  type        = bool
  default     = false
}

variable "adopt_existing_field_ownership" {
  description = "One-time, explicitly reviewed field ownership transfer from the retired updater. Return to false after adoption."
  type        = bool
  default     = false
}

variable "rag_enabled" {
  description = "Add the shared RAG dashboard after adopting the existing configuration."
  type        = bool
  default     = false
}

variable "prometheus_site_path" {
  description = "Owner-only existing O1/O2/OpenWrt site JSON outside Git; enables adoption of three Prometheus configuration resources."
  type        = string
  default     = ""
}

variable "rag_address" {
  description = "Specific private IPv4 of the RAG metrics listener; fixed port 3102."
  type        = string
  default     = ""
}

variable "existing_folder_uid" {
  description = "Existing Staging folder UID from Grafana API inventory; empty preserves the baseline."
  type        = string
  default     = ""
  validation {
    condition     = var.existing_folder_uid == "" || can(regex("^[A-Za-z0-9_-]{1,40}$", var.existing_folder_uid))
    error_message = "Invalid Grafana folder UID."
  }
}
