mock_provider "kubernetes" {
  mock_data "kubernetes_namespace_v1" {
    defaults = {
      metadata = { uid = "11111111-1111-1111-1111-111111111111" }
    }
  }
}

mock_provider "external" {
  mock_data "external" {
    defaults = { result = { manifest = "{\"items\":[]}" } }
  }
}

variables {
  kubeconfig_path          = "/unused/mock-kubeconfig"
  kubeconfig_context       = "mock-prod"
  expected_kube_system_uid = "11111111-1111-1111-1111-111111111111"
}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(kubernetes_service_v1.typevt_node_exporter_tailnet) == 0
    error_message = "Existing callers must not create an egress proxy."
  }
}

run "dedicated_production_route" {
  command = plan
  variables {
    typevt_tailnet_fqdn  = "tablet.tail0000.ts.net"
    prometheus_site_path = "/private/mock-site.json"
  }
  assert {
    condition = (
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].metadata[0].namespace == "observability-prod" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].metadata[0].annotations["tailscale.com/tailnet-fqdn"] == "tablet.tail0000.ts.net" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].spec[0].type == "ExternalName" &&
      kubernetes_service_v1.typevt_node_exporter_tailnet[0].spec[0].port[0].port == 9100 &&
      data.external.grafana.query.typevt_host_address == "typevt-node-exporter-tailnet.observability-prod.svc.cluster.local"
    )
    error_message = "TypeVT must use the dedicated production Service on TCP 9100."
  }
}

run "reject_ambiguous_direct_route" {
  command = plan
  variables {
    typevt_tailnet_fqdn  = "tablet.tail0000.ts.net"
    typevt_host_address  = "100.64.0.42"
    prometheus_site_path = "/private/mock-site.json"
  }
  expect_failures = [kubernetes_service_v1.typevt_node_exporter_tailnet]
}

run "reject_missing_monitoring_site" {
  command = plan
  variables { typevt_tailnet_fqdn = "tablet.tail0000.ts.net" }
  expect_failures = [kubernetes_service_v1.typevt_node_exporter_tailnet]
}

run "reject_wrong_cluster_identity" {
  command = plan
  variables {
    expected_kube_system_uid = "22222222-2222-2222-2222-222222222222"
    typevt_tailnet_fqdn      = "tablet.tail0000.ts.net"
    prometheus_site_path     = "/private/mock-site.json"
  }
  expect_failures = [kubernetes_service_v1.typevt_node_exporter_tailnet]
}

run "reject_public_destination" {
  command = plan
  variables { typevt_tailnet_fqdn = "example.com" }
  expect_failures = [var.typevt_tailnet_fqdn]
}
