variable "zone_id" {
  type        = string
  description = "Cloudflare zone identifier. Keep the real zone ID in private site input."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.zone_id))
    error_message = "zone_id must be a 32-character hexadecimal Cloudflare zone identifier."
  }
}

variable "zone_name" {
  type        = string
  description = "Cloudflare zone name used by Email Routing DNS enablement. Keep the real zone name in private site input."

  validation {
    condition     = trimspace(var.zone_name) != "" && length(var.zone_name) <= 253
    error_message = "zone_name must be a non-empty DNS zone name no longer than 253 characters."
  }
}

variable "rules" {
  description = "Explicit Email Routing rules keyed by stable Terraform identity. Match addresses and forward destinations are private site input."

  type = map(object({
    name     = string
    enabled  = bool
    priority = number
    matcher = object({
      type  = string
      field = string
      value = string
    })
    action = object({
      type  = string
      value = optional(list(string), [])
    })
  }))

  default = {}

  validation {
    condition = alltrue([
      for rule in values(var.rules) :
      trimspace(rule.name) != "" && length(rule.name) <= 256
    ])
    error_message = "Every Email Routing rule name must be non-empty and no longer than 256 characters."
  }

  validation {
    condition = alltrue([
      for rule in values(var.rules) :
      lower(rule.matcher.type) == "literal" &&
      lower(rule.matcher.field) == "to" &&
      trimspace(rule.matcher.value) != "" &&
      length(rule.matcher.value) <= 90
    ])
    error_message = "Initial Email Routing rules must use matcher type=literal, field=to, and a non-empty value no longer than 90 characters."
  }

  validation {
    condition = alltrue([
      for rule in values(var.rules) :
      rule.priority >= 0
    ])
    error_message = "Email Routing rule priority must be zero or greater."
  }

  validation {
    condition = alltrue([
      for rule in values(var.rules) :
      contains(["forward", "drop"], lower(rule.action.type))
    ])
    error_message = "Initial Email Routing rule actions are limited to forward or drop."
  }

  validation {
    condition = alltrue([
      for rule in values(var.rules) :
      lower(rule.action.type) == "forward"
      ? length(rule.action.value) == 1 && trimspace(rule.action.value[0]) != ""
      : length(rule.action.value) == 0
    ])
    error_message = "forward requires exactly one non-empty destination; drop must omit action values."
  }
}

variable "catch_all" {
  description = "Desired catch-all Email Routing rule. A forward action must reference exactly one already-verified external destination address."

  type = object({
    name    = string
    enabled = bool
    action = object({
      type  = string
      value = optional(list(string), [])
    })
  })

  validation {
    condition     = trimspace(var.catch_all.name) != "" && length(var.catch_all.name) <= 256
    error_message = "catch_all.name must be non-empty and no longer than 256 characters."
  }

  validation {
    condition     = contains(["forward", "drop"], lower(var.catch_all.action.type))
    error_message = "catch_all.action.type must be forward or drop."
  }

  validation {
    condition = (
      lower(var.catch_all.action.type) == "forward"
      ? length(var.catch_all.action.value) == 1 && trimspace(var.catch_all.action.value[0]) != ""
      : length(var.catch_all.action.value) == 0
    )
    error_message = "catch-all forward requires exactly one non-empty destination; drop must omit action values."
  }
}
