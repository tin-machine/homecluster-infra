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
