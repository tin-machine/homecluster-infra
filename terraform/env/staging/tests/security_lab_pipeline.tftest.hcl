# No credentials or cluster: exercise actual Terraform planned resource data.
mock_provider "kubernetes" {
  mock_data "kubernetes_nodes" {
    defaults = { nodes = [{ "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-worker", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-worker", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-control-plane", "node-role.kubernetes.io/worker" : "true", "node-role.kubernetes.io/control-plane" : "true" }, "name" : "fixture-control-plane", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [{ "address" : "192.0.2.2", "type" : "InternalIP" }, { "address" : "fixture-control-plane", "type" : "Hostname" }], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-unknown", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-unknown", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-pressure", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-pressure", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-suspended", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-suspended", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : true }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-tainted", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-tainted", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [{ "key" : "fixture", "value" : "blocked", "effect" : "NoSchedule" }], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-wrong-arch", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-wrong-arch", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }], "node_info" : [{ "architecture" : "amd64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }, { "metadata" : [{ "annotations" : {}, "generation" : 1, "labels" : { "kubernetes.io/hostname" : "fixture-worker", "node-role.kubernetes.io/worker" : "true" }, "name" : "fixture-duplicate", "resource_version" : "1", "uid" : "fixture-worker-uid" }], "spec" : [{ "pod_cidr" : "192.0.2.0/24", "pod_cidrs" : ["192.0.2.0/24"], "provider_id" : "", "taints" : [], "unschedulable" : false }], "status" : [{ "addresses" : [], "allocatable" : { "cpu" : "4", "memory" : "8Gi" }, "capacity" : { "cpu" : "4", "memory" : "8Gi" }, "conditions" : [{ "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "True", "type" : "Ready" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "MemoryPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "DiskPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "PIDPressure" }, { "last_heartbeat_time" : "2026-01-01T00:00:00Z", "last_transition_time" : "2026-01-01T00:00:00Z", "message" : "fixture", "reason" : "fixture", "status" : "False", "type" : "Ready" }], "node_info" : [{ "architecture" : "arm64", "boot_id" : "fixture", "container_runtime_version" : "containerd://fixture", "kernel_version" : "fixture", "kube_proxy_version" : "", "kubelet_version" : "v1.36.4", "machine_id" : "fixture", "operating_system" : "linux", "os_image" : "fixture", "system_uuid" : "fixture" }] }] }] }
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

run "pipeline_default_off" {
  command = plan
  plan_options {
    refresh = false
    target  = [kubernetes_config_map_v1.security_lab_runtime, kubernetes_deployment_v1.security_lab_coordinator, kubernetes_priority_class_v1.security_lab_background]
  }
  assert {
    condition     = length(kubernetes_deployment_v1.security_lab_coordinator) == 0 && length(kubernetes_config_map_v1.security_lab_runtime) == 0
    error_message = "The pipeline must remain disabled by default."
  }
}
run "pipeline_restricted_and_pinned" {
  command = plan
  variables {
    security_lab_offline_enabled   = true
    security_lab_worker_node       = "fixture-worker"
    security_lab_pipeline_enabled  = true
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
    security_lab_home_nets         = ["192.0.2.0/24"]
  }
  plan_options {
    refresh = false
    target  = [kubernetes_config_map_v1.security_lab_runtime, kubernetes_deployment_v1.security_lab_coordinator, kubernetes_priority_class_v1.security_lab_background]
  }
  assert {
    condition     = kubernetes_config_map_v1.security_lab_runtime[0].immutable && can(regex("@sha256:[a-f0-9]{64}$", jsondecode(kubernetes_config_map_v1.security_lab_runtime[0].data["settings.json"]).images.zeek))
    error_message = "Runtime configuration and all images must be immutable."
  }
  assert {
    condition     = jsondecode(kubernetes_config_map_v1.security_lab_runtime[0].data["settings.json"]).nfs_version == 3
    error_message = "The required actual NFS version must reach the runtime mount validator."
  }
  assert {
    condition     = !jsondecode(kubernetes_config_map_v1.security_lab_runtime[0].data["settings.json"]).retention_enabled && contains(keys(kubernetes_config_map_v1.security_lab_runtime[0].data), "retention.py")
    error_message = "Ship the immutable retention mechanism but keep automatic payload cleanup explicitly opt-in."
  }
  assert {
    condition     = tonumber(kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].replicas) == 1 && kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].strategy[0].type == "Recreate" && !kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].template[0].spec[0].automount_service_account_token
    error_message = "Only one coordinator may own the projected, restricted API token."
  }
  assert {
    condition     = kubernetes_priority_class_v1.security_lab_background[0].value == -1000 && kubernetes_priority_class_v1.security_lab_background[0].preemption_policy == "Never" && !kubernetes_priority_class_v1.security_lab_background[0].global_default
    error_message = "Analysis must wait for existing workloads without preempting them."
  }
  assert {
    condition     = tonumber(kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].template[0].spec[0].security_context[0].run_as_user) == 10001 && kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].template[0].spec[0].container[0].security_context[0].read_only_root_filesystem
    error_message = "Coordinator must use the validated non-root UID and read-only root."
  }
  assert {
    condition     = kubernetes_deployment_v1.security_lab_coordinator[0].spec[0].template[0].spec[0].container[0].security_context[0].run_as_non_root == true
    error_message = "The provider must explicitly emit container runAsNonRoot=true; its false default overrides the restricted Pod setting."
  }
  assert {
    condition     = strcontains(kubernetes_config_map_v1.security_lab_runtime[0].data["suricata.yaml"], "[192.0.2.0/24]") && !strcontains(kubernetes_config_map_v1.security_lab_runtime[0].data["suricata.yaml"], "@HOME_NET@")
    error_message = "The explicit HOME_NET must replace the configuration placeholder."
  }
}
run "reject_unknown_nfs_version" {
  command = plan
  variables {
    security_lab_nfs_version = 0
  }
  expect_failures = [var.security_lab_nfs_version]
}

run "retention_opt_in" {
  command = plan
  variables {
    security_lab_offline_enabled   = true
    security_lab_worker_node       = "fixture-worker"
    security_lab_pipeline_enabled  = true
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
    security_lab_home_nets         = ["192.0.2.0/24"]
    security_lab_retention_enabled = true
  }
  plan_options {
    refresh = false
    target  = [kubernetes_config_map_v1.security_lab_runtime, kubernetes_deployment_v1.security_lab_coordinator]
  }
  assert {
    condition     = jsondecode(kubernetes_config_map_v1.security_lab_runtime[0].data["settings.json"]).retention_enabled
    error_message = "An explicit retention opt-in must reach the coordinator."
  }
}

run "retention_requires_pipeline" {
  command = plan
  variables {
    security_lab_retention_enabled = true
  }
  expect_failures = [var.security_lab_retention_enabled]
}
run "reject_pipeline_without_home_net" {
  command = plan
  variables {
    security_lab_offline_enabled   = true
    security_lab_worker_node       = "fixture-worker"
    security_lab_pipeline_enabled  = true
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
  }
  plan_options {
    refresh = false
    target  = [kubernetes_config_map_v1.security_lab_runtime]
  }
  expect_failures = [kubernetes_config_map_v1.security_lab_runtime]
}
run "reject_invalid_home_net" {
  command = plan
  variables {
    security_lab_offline_enabled   = true
    security_lab_worker_node       = "fixture-worker"
    security_lab_pipeline_enabled  = true
    security_lab_logs_enabled      = true
    o4_prod_container_logs_enabled = true
    security_lab_home_nets         = ["not-a-cidr"]
  }
  plan_options {
    refresh = false
    target  = [kubernetes_config_map_v1.security_lab_runtime]
  }
  expect_failures = [kubernetes_config_map_v1.security_lab_runtime]
}
run "reject_root_uid" {
  command = plan
  variables {
    security_lab_pipeline_uid = 0
  }
  expect_failures = [var.security_lab_pipeline_uid]
}

run "coordinator_requires_log_delivery" {
  command = plan
  variables {
    security_lab_offline_enabled  = true
    security_lab_worker_node      = "fixture-worker"
    security_lab_pipeline_enabled = true
    security_lab_home_nets        = ["192.0.2.0/24"]
  }
  plan_options {
    refresh = false
    target  = [kubernetes_deployment_v1.security_lab_coordinator]
  }
  expect_failures = [kubernetes_deployment_v1.security_lab_coordinator]
}
