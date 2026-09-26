resource "cloudflare_dns_record" "record" {
  for_each = var.records

  zone_id  = var.zone_id
  name     = each.value.name
  type     = upper(each.value.type)
  content  = each.value.content
  ttl      = each.value.ttl
  proxied  = each.value.proxied
  priority = each.value.priority
  comment  = each.value.comment
  tags     = each.value.tags
}
