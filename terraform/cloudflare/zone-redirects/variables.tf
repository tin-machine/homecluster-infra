variable "zone_id" {
  type        = string
  description = "Cloudflare zone ID."
  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.zone_id))
    error_message = "zone_id must contain exactly 32 hexadecimal characters."
  }
}

variable "redirects" {
  description = "Stable rule references mapped to canonical source and target hostnames."
  type = map(object({
    source_hostname = string
    target_hostname = string
  }))
  nullable = false

  validation {
    condition     = length(var.redirects) > 0
    error_message = "At least one redirect is required."
  }
  validation {
    condition     = alltrue([for ref in keys(var.redirects) : can(regex("^[a-z][a-z0-9_]{0,63}$", ref))])
    error_message = "Rule references must be stable lowercase identifiers."
  }
  validation {
    condition = alltrue(flatten([for rule in values(var.redirects) : [
      for hostname in [rule.source_hostname, rule.target_hostname] : try(
        length(hostname) <= 253 && can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", hostname)), false
      )
    ]]))
    error_message = "Hostnames must be canonical lowercase DNS names with at least two labels, each 1..63 characters, total at most 253."
  }
  validation {
    condition     = alltrue([for rule in values(var.redirects) : rule.source_hostname != rule.target_hostname])
    error_message = "Source and target hostnames must differ."
  }
  validation {
    condition     = length(distinct([for rule in values(var.redirects) : rule.source_hostname])) == length(var.redirects)
    error_message = "Source hostnames must be unique."
  }
  validation {
    condition     = alltrue([for rule in values(var.redirects) : !contains([for entry in values(var.redirects) : entry.source_hostname], rule.target_hostname)])
    error_message = "Redirect chains and cycles are forbidden."
  }
}
