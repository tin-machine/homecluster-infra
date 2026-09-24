variable "zone_id" {
  type        = string
  description = "Cloudflare zone identifier. Keep the real zone ID in private site input."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.zone_id))
    error_message = "zone_id must be a 32-character hexadecimal Cloudflare zone identifier."
  }
}

variable "records" {
  description = "Cloudflare DNS records keyed by stable Terraform identity. Record names and contents are site input, not public defaults."

  type = map(object({
    name     = string
    type     = string
    content  = string
    ttl      = optional(number, 300)
    proxied  = optional(bool, false)
    priority = optional(number)
    comment  = optional(string)
    tags     = optional(set(string), [])
  }))

  validation {
    condition     = length(var.records) > 0
    error_message = "records must contain at least one DNS record."
  }

  validation {
    condition = alltrue([
      for record in values(var.records) :
      trimspace(record.name) != "" && trimspace(record.content) != ""
    ])
    error_message = "Every DNS record must have a non-empty name and content."
  }

  validation {
    condition = alltrue([
      for record in values(var.records) :
      contains(["A", "AAAA", "CNAME", "MX", "TXT"], upper(record.type))
    ])
    error_message = "Initial Cloudflare DNS support is limited to A, AAAA, CNAME, MX, and TXT records."
  }

  validation {
    condition = alltrue([
      for record in values(var.records) :
      record.ttl == 1 || (record.ttl >= 60 && record.ttl <= 86400)
    ])
    error_message = "ttl must be 1 (automatic) or between 60 and 86400 seconds."
  }

  validation {
    condition = alltrue([
      for record in values(var.records) :
      !record.proxied || (
        contains(["A", "AAAA", "CNAME"], upper(record.type)) &&
        record.ttl == 1
      )
    ])
    error_message = "Only A, AAAA, and CNAME records may be proxied, and proxied records must use ttl = 1."
  }

  validation {
    condition = alltrue([
      for record in values(var.records) :
      upper(record.type) == "MX" ? record.priority != null : record.priority == null
    ])
    error_message = "MX records require priority; other initially supported record types must omit it."
  }
}
