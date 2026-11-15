mock_provider "cloudflare" {}

variables {
  zone_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  redirects = {
    z_second = { source_hostname = "other.example.net", target_hostname = "www.other.example.net" }
    a_first  = { source_hostname = "example.net", target_hostname = "www.example.net" }
  }
}

run "canonical_redirects" {
  command = plan
  assert {
    condition     = cloudflare_ruleset.single_redirects.zone_id == var.zone_id && cloudflare_ruleset.single_redirects.kind == "zone" && cloudflare_ruleset.single_redirects.phase == "http_request_dynamic_redirect"
    error_message = "Ruleset must own the exact zone and phase."
  }
  assert {
    condition     = [for rule in cloudflare_ruleset.single_redirects.rules : rule.ref] == ["a_first", "z_second"]
    error_message = "References must be stable and sorted."
  }
  assert {
    condition     = alltrue([for rule in cloudflare_ruleset.single_redirects.rules : rule.action == "redirect" && rule.enabled && rule.action_parameters.from_value.status_code == 301 && rule.action_parameters.from_value.preserve_query_string])
    error_message = "Redirects must be enabled 301 rules preserving query strings."
  }
  assert {
    condition     = cloudflare_ruleset.single_redirects.rules[0].expression == "http.host eq \"example.net\"" && cloudflare_ruleset.single_redirects.rules[0].action_parameters.from_value.target_url.expression == "concat(\"https://www.example.net\", http.request.uri.path)"
    error_message = "Exact source matching and path preservation are required."
  }
}

run "reject_empty" {
  command = plan
  variables {
    redirects = {}
  }
  expect_failures = [var.redirects]
}

run "reject_uppercase" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "Example.net", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_path" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "example.net/path", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_wildcard" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "*.example.net", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_self" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "example.net", target_hostname = "example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_duplicate" {
  command = plan
  variables {
    redirects = { a = { source_hostname = "example.net", target_hostname = "www.example.net" }, b = { source_hostname = "example.net", target_hostname = "other.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_cycle" {
  command = plan
  variables {
    redirects = { a = { source_hostname = "example.net", target_hostname = "other.example.net" }, b = { source_hostname = "other.example.net", target_hostname = "example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_chain" {
  command = plan
  variables {
    redirects = { a = { source_hostname = "example.net", target_hostname = "other.example.net" }, b = { source_hostname = "other.example.net", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_invalid_ref" {
  command = plan
  variables {
    redirects = { "Bad-Ref" = { source_hostname = "example.net", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_long_label" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.net", target_hostname = "www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_bad_target" {
  command = plan
  variables {
    redirects = { bad = { source_hostname = "example.net", target_hostname = "https://www.example.net" } }
  }
  expect_failures = [var.redirects]
}

run "reject_zone" {
  command = plan
  variables {
    zone_id = "invalid"
  }
  expect_failures = [var.zone_id]
}
