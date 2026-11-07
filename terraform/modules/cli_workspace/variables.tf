variable "namespace" {
  type    = string
  default = "cli-tools"
}

variable "name" {
  type    = string
  default = "cli-tool"
}

variable "image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "CLI workspace image must be pinned to a sha256 digest."
  }
}

variable "node_name" {
  type = string
  validation {
    condition     = length(trimspace(var.node_name)) > 0
    error_message = "A workspace node name is required."
  }
}

variable "nas_server" {
  type = string
  validation {
    condition     = length(trimspace(var.nas_server)) > 0
    error_message = "The NAS server is required."
  }
}

variable "nas_export_path" {
  type = string
  validation {
    condition     = startswith(var.nas_export_path, "/") && !strcontains(var.nas_export_path, "..")
    error_message = "The NAS export path must be absolute and cannot contain traversal."
  }
}

variable "nas_home_subpath" {
  type = string
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9._-]*$", var.nas_home_subpath)) && var.nas_home_subpath != ".."
    error_message = "The NAS home subpath must be one safe directory name."
  }
}

variable "home_mount_path" {
  type        = string
  description = "Container home path matching existing absolute dotfile symlinks when needed."
  default     = "/work-home"
  validation {
    condition = (
      var.home_mount_path == "/work-home" ||
      (can(regex("^/(home|net|mnt)/[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)*$", var.home_mount_path)) &&
      alltrue([for part in split("/", var.home_mount_path) : !contains([".", ".."], part)]))
    )
    error_message = "Home must be /work-home or a path below /home, /net, or /mnt without dot segments."
  }
}

variable "user_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]*$", var.user_name))
    error_message = "The workspace user name must be a local Unix account name."
  }
}

variable "user_id" {
  type = number
  validation {
    condition     = var.user_id > 0 && floor(var.user_id) == var.user_id
    error_message = "The workspace UID must be a nonzero integer."
  }
}

variable "group_id" {
  type = number
  validation {
    condition     = var.group_id > 0 && floor(var.group_id) == var.group_id
    error_message = "The workspace GID must be a nonzero integer."
  }
}

variable "state_storage_size" {
  type    = string
  default = "10Gi"
}

variable "ssh_enabled" {
  type    = bool
  default = false
}

variable "ssh_image" {
  type    = string
  default = ""
  validation {
    condition     = !var.ssh_enabled || can(regex("@sha256:[0-9a-f]{64}$", var.ssh_image))
    error_message = "The SSH sidecar image must be pinned by sha256 when enabled."
  }
}

variable "ssh_authorized_keys_secret_name" {
  type    = string
  default = "cli-workspace-ssh"
  validation {
    condition     = length(var.ssh_authorized_keys_secret_name) <= 63 && can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.ssh_authorized_keys_secret_name))
    error_message = "SSH authorized_keys Secret name must be a Kubernetes DNS label."
  }
}

variable "ssh_tailnet_hostname" {
  type    = string
  default = "workspace"
  validation {
    condition     = length(var.ssh_tailnet_hostname) <= 63 && can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.ssh_tailnet_hostname))
    error_message = "The tailnet hostname must be one DNS label."
  }
}

variable "environment" {
  type    = string
  default = "staging"
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Environment must be staging or production."
  }
}

variable "replicas" {
  type    = number
  default = 1
  validation {
    condition     = contains([0, 1], var.replicas)
    error_message = "Workspace replicas must be 0 or 1 to prevent concurrent writers."
  }
}

variable "ssh_tailnet_tags" {
  type    = list(string)
  default = ["tag:cli-workspace-stg"]
  validation {
    condition     = length(var.ssh_tailnet_tags) > 0 && alltrue([for tag in var.ssh_tailnet_tags : can(regex("^tag:[a-z0-9][a-z0-9-]*$", tag))])
    error_message = "At least one valid tag:<name> is required."
  }
}

variable "cpu_limit" {
  type    = string
  default = null
  validation {
    condition     = var.cpu_limit == null ? true : can(regex("^[1-9][0-9]*(m)?$", var.cpu_limit))
    error_message = "CPU limit must be null, positive whole cores, or positive millicores."
  }
}

variable "memory_limit" {
  type    = string
  default = "4Gi"
  validation {
    condition     = can(regex("^[1-9][0-9]*(Mi|Gi)$", var.memory_limit))
    error_message = "Memory limit must be a positive Mi or Gi quantity."
  }
}

variable "nas_mount_options" {
  type        = list(string)
  default     = []
  description = "Nonempty options select a static NFS PV; empty preserves inline NFS."
}

variable "runtime_class_name" {
  type        = string
  default     = null
  description = "Optional preconfigured RuntimeClass, for example a dedicated snapshotter."
  validation {
    condition     = var.runtime_class_name == null ? true : (length(var.runtime_class_name) <= 63 && can(regex("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", var.runtime_class_name)))
    error_message = "RuntimeClass must be null or a Kubernetes DNS label."
  }
}
