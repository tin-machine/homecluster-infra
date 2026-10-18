variable "environment" {
  type        = string
  description = "Environment label value"

  validation {
    condition     = var.environment != null
    error_message = "environment must not be null because it is used in Namespace labels."
  }
}

variable "namespace" {
  type        = string
  description = "Namespace for the registry"

  validation {
    condition     = var.namespace != null
    error_message = "namespace must not be null because it is used in the Namespace name and labels."
  }
}

variable "namespace_app_name" {
  type        = string
  description = "app.kubernetes.io/name label for the namespace"

  validation {
    condition     = var.namespace_app_name != null
    error_message = "namespace_app_name must not be null because it is used in Namespace labels."
  }
}

variable "name" {
  type        = string
  description = "Name used for the registry Deployment, Service, and PVC"
}

variable "part_of" {
  type        = string
  description = "app.kubernetes.io/part-of label for registry resources"

  validation {
    condition     = var.part_of != null
    error_message = "part_of must not be null because it is used in Namespace labels."
  }
}

variable "image" {
  type        = string
  description = "Registry image"
}

variable "replicas" {
  type        = number
  description = "Number of registry replicas"

  validation {
    condition     = var.replicas == 1
    error_message = "homelab registry currently supports exactly one replica."
  }
}

variable "node_selector" {
  type        = map(string)
  description = "Node selector for registry workload placement"
}

variable "storage_class_name" {
  type        = string
  description = "StorageClass for registry blob storage"
}

variable "storage_size" {
  type        = string
  description = "PVC size for registry blob storage"
}

variable "load_balancer_ip" {
  type        = string
  description = "Static MetalLB BGP VIP for the registry LoadBalancer service"
}

variable "port" {
  type        = number
  description = "Registry TCP port"
}
