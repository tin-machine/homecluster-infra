# No credentials or cluster: exercise actual Terraform planned resource data.
mock_provider "kubernetes" {
  mock_data "kubernetes_nodes" {
    defaults = { nodes = jsondecode(file("tests/fixtures/security-nodes.json")) }
  }
  mock_data "kubernetes_service_v1" {
    defaults = { spec = [{ cluster_ip = "192.0.2.1" }] }
  }
}
mock_provider "helm" {}

variables {
  kubeconfig_path              = "/dev/null"
  minio_root_user              = "namespace-fixture"
  minio_root_password          = "namespace-fixture-not-a-credential"
  grafana_load_balancer_ip     = "192.0.2.10"
  grafana_admin_user           = "namespace-fixture"
  grafana_admin_password       = "namespace-fixture-not-a-credential"
  otel_syslog_load_balancer_ip = "192.0.2.11"
  registry_namespace_app_name  = "registry"
  registry_part_of             = "registry-stack"
  registry_node_selector       = {}
  registry_load_balancer_ip    = "192.0.2.12"
  workload_node_selector       = { "fixture.example.com/role" = "worker" }
  site_values_dir              = "../../../clusters/homelab/apps/staging"
}

run "default_disabled_preserves_existing_namespace" {
  command = plan
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }

  assert {
    condition     = length(kubernetes_labels.security_lab_worker) == 0 && length(kubernetes_service_account_v1.security_lab_launcher) == 0
    error_message = "Offline analysis must remain opt-in."
  }
  assert {
    condition     = !contains(keys(kubernetes_namespace_v1.security_lab.metadata[0].labels), "pod-security.kubernetes.io/enforce") && length(kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress.spec[0].egress) == 2
    error_message = "Default-disabled must preserve current namespace metadata and egress."
  }
}

run "explicit_offline_restricts_namespace_and_permissions" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-worker"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }

  assert {
    condition     = kubernetes_labels.security_lab_worker[0].kind == "Node" && kubernetes_labels.security_lab_worker[0].metadata[0].name == "fixture-worker" && kubernetes_labels.security_lab_worker[0].labels == tomap({ "security-lab.home.arpa/analysis" = "true" }) && kubernetes_labels.security_lab_worker[0].force == false
    error_message = "Only the eligible worker's analysis label may be managed without forcing other labels."
  }
  assert {
    condition     = kubernetes_namespace_v1.security_lab.metadata[0].labels["pod-security.kubernetes.io/enforce"] == "restricted" && length(kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress.spec[0].egress) == 0
    error_message = "Offline analysis must enforce restricted Pods and remove broad DNS/Loki egress."
  }
  assert {
    condition = kubernetes_service_account_v1.security_lab_launcher[0].automount_service_account_token == false && alltrue([
      for rule in kubernetes_role_v1.security_lab_launcher[0].rule : alltrue([
        for resource in rule.resources : contains(["jobs", "pods", "pods/log"], resource)
      ]) && !contains(rule.verbs, "*") && !contains(rule.resources, "secrets")
    ]) && toset(kubernetes_cluster_role_v1.security_lab_node_reader[0].rule[0].resource_names) == toset(["fixture-worker"]) && toset(kubernetes_cluster_role_v1.security_lab_node_reader[0].rule[0].verbs) == toset(["get"])
    error_message = "Launcher credentials may only manage analysis jobs and read their Pods and the selected node."
  }
  assert {
    condition = kubernetes_network_policy_v1.security_lab_coordinator_api[0].spec[0].pod_selector[0].match_labels == tomap({ "app.kubernetes.io/component" = "security-coordinator" }) && alltrue([
      for egress in kubernetes_network_policy_v1.security_lab_coordinator_api[0].spec[0].egress : length(egress.to) == 1 && length(egress.ports) == 1 && (
        (egress.to[0].ip_block[0].cidr == "192.0.2.1/32" && egress.ports[0].port == "443") ||
        (egress.to[0].ip_block[0].cidr == "192.0.2.2/32" && egress.ports[0].port == "6443")
      )
    ])
    error_message = "Only coordinator API traffic may bypass default-deny, never DNS, Loki, or a broad LAN/WAN range."
  }
}

run "reject_control_plane" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-control-plane"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_unknown" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-unknown"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_pressure" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-pressure"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_suspended" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-suspended"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_tainted" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-tainted"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_wrong_arch" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-wrong-arch"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_missing" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-missing"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_duplicate" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-duplicate"
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "reject_missing_control_plane_ip" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-worker"
  }
  override_data {
    target = data.kubernetes_nodes.security_lab[0]
    values = {
      nodes = [for node in jsondecode(file("tests/fixtures/security-nodes.json")) : merge(node, {
        status = [merge(node.status[0], { addresses = [] })]
      })]
    }
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  expect_failures = [kubernetes_labels.security_lab_worker]
}

run "ipv6_api_addresses_remain_host_scoped" {
  command = plan
  variables {
    security_lab_offline_enabled = true
    security_lab_worker_node     = "fixture-worker"
  }
  override_data {
    target = data.kubernetes_service_v1.security_lab_api[0]
    values = { spec = [{ cluster_ip = "2001:db8::1" }] }
  }
  override_data {
    target = data.kubernetes_nodes.security_lab[0]
    values = {
      nodes = [for node in jsondecode(file("tests/fixtures/security-nodes.json")) : merge(node, {
        status = [merge(node.status[0], { addresses = [{ address = "2001:db8::2", type = "InternalIP" }] })]
      })]
    }
  }
  plan_options {
    refresh = false
    target = [
      kubernetes_namespace_v1.security_lab,
      kubernetes_network_policy_v1.security_lab_allow_dns_and_loki_egress,
      kubernetes_labels.security_lab_worker,
      kubernetes_service_account_v1.security_lab_launcher,
      kubernetes_role_v1.security_lab_launcher,
      kubernetes_role_binding_v1.security_lab_launcher,
      kubernetes_cluster_role_v1.security_lab_node_reader,
      kubernetes_cluster_role_binding_v1.security_lab_node_reader,
      kubernetes_network_policy_v1.security_lab_coordinator_api,
    ]
  }
  assert {
    condition     = toset([for rule in kubernetes_network_policy_v1.security_lab_coordinator_api[0].spec[0].egress : rule.to[0].ip_block[0].cidr]) == toset(["2001:db8::1/128", "2001:db8::2/128"])
    error_message = "IPv6 API egress must use exact host /128 addresses, never a network prefix."
  }
}
