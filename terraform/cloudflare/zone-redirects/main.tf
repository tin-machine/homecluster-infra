resource "cloudflare_ruleset" "single_redirects" {
  zone_id = var.zone_id
  name    = "managed-single-redirects"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [for ref in sort(keys(var.redirects)) : {
    ref         = ref
    description = "Canonical hostname redirect"
    action      = "redirect"
    enabled     = true
    expression  = "http.host eq ${jsonencode(var.redirects[ref].source_hostname)}"
    action_parameters = {
      from_value = {
        status_code           = 301
        preserve_query_string = true
        target_url = {
          expression = "concat(${jsonencode("https://${var.redirects[ref].target_hostname}")}, http.request.uri.path)"
        }
      }
    }
  }]

  lifecycle {
    prevent_destroy = true
  }
}
