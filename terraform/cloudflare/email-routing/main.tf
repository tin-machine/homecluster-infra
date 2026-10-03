resource "cloudflare_email_routing_dns" "zone" {
  zone_id = var.zone_id
  name    = var.zone_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_email_routing_rule" "rule" {
  for_each = var.rules

  zone_id = var.zone_id

  actions = [{
    type  = lower(each.value.action.type)
    value = lower(each.value.action.type) == "forward" ? each.value.action.value : null
  }]

  matchers = [{
    type  = "literal"
    field = "to"
    value = each.value.matcher.value
  }]

  enabled  = each.value.enabled
  name     = each.value.name
  priority = each.value.priority
  source   = "api"

  depends_on = [cloudflare_email_routing_dns.zone]
}

resource "cloudflare_email_routing_catch_all" "zone" {
  zone_id = var.zone_id

  actions = [{
    type  = lower(var.catch_all.action.type)
    value = lower(var.catch_all.action.type) == "forward" ? var.catch_all.action.value : null
  }]

  matchers = [{
    type = "all"
  }]

  enabled = var.catch_all.enabled
  name    = var.catch_all.name
  source  = "api"

  depends_on = [cloudflare_email_routing_dns.zone]
}
