variable "account_id" {
  type        = string
  description = "Cloudflare account identifier. Keep the real account ID in private site input."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.account_id))
    error_message = "account_id must be a 32-character hexadecimal Cloudflare account identifier."
  }
}

variable "zone_id" {
  type        = string
  description = "Cloudflare zone identifier containing both WorldWeaver hostnames. Keep the real zone ID in private site input."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.zone_id))
    error_message = "zone_id must be a 32-character hexadecimal Cloudflare zone identifier."
  }
}

variable "application_hostname" {
  type        = string
  description = "Production hostname routed to the existing WorldWeaver Worker service."

  validation {
    condition = (
      trimspace(var.application_hostname) != "" &&
      length(var.application_hostname) <= 253 &&
      strcontains(trimspace(var.application_hostname), ".")
    )
    error_message = "application_hostname must be a non-empty DNS hostname containing a dot and no longer than 253 characters."
  }
}

variable "worker_service_name" {
  type        = string
  description = "Existing Cloudflare Worker service deployed by the WorldWeaver application release workflow."

  validation {
    condition     = trimspace(var.worker_service_name) != ""
    error_message = "worker_service_name must be non-empty."
  }
}

variable "asset_hostname" {
  type        = string
  description = "Production hostname for the existing R2 bucket containing immutable public WorldWeaver assets."

  validation {
    condition = (
      trimspace(var.asset_hostname) != "" &&
      length(var.asset_hostname) <= 253 &&
      strcontains(trimspace(var.asset_hostname), ".")
    )
    error_message = "asset_hostname must be a non-empty DNS hostname containing a dot and no longer than 253 characters."
  }
}

variable "asset_bucket_name" {
  type        = string
  description = "Existing R2 bucket name. This Terraform root binds a custom domain but does not create or own the bucket."

  validation {
    condition     = trimspace(var.asset_bucket_name) != ""
    error_message = "asset_bucket_name must be non-empty."
  }
}
