resource "cloudflare_workers_custom_domain" "application" {
  account_id = var.account_id
  hostname   = var.application_hostname
  service    = var.worker_service_name
  zone_id    = var.zone_id

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = lower(trimspace(var.application_hostname)) != lower(trimspace(var.asset_hostname))
      error_message = "application_hostname and asset_hostname must be different hostnames."
    }
  }
}

resource "cloudflare_r2_custom_domain" "assets" {
  account_id  = var.account_id
  bucket_name = var.asset_bucket_name
  domain      = var.asset_hostname
  enabled     = true
  zone_id     = var.zone_id
  min_tls     = "1.2"

  lifecycle {
    prevent_destroy = true
  }
}
