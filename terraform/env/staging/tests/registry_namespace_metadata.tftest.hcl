# Use the real Kubernetes provider: mocking would bypass its validators.
# The CI launcher clears KUBE_* / TF_* inputs and uses an isolated checkout.
provider "kubernetes" {
  host = "http://127.0.0.1:1"
}

mock_provider "helm" {}

variables {
  environment        = "staging"
  namespace          = "registry-fixture"
  namespace_app_name = "registry"
  name               = "registry-fixture"
  part_of            = "registry-stack"
  image              = "registry:2"
  replicas           = 1
  node_selector      = {}
  storage_class_name = "fixture-storage"
  storage_size       = "1Gi"
  load_balancer_ip   = "192.0.2.12"
  port               = 5000
}

run "registry_namespace_metadata" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  assert {
    condition     = kubernetes_namespace_v1.this.metadata[0].name == "registry-fixture"
    error_message = "The registry Namespace name must be preserved."
  }

  assert {
    condition = kubernetes_namespace_v1.this.metadata[0].labels == tomap({
      "app.kubernetes.io/name"     = "registry"
      "app.kubernetes.io/instance" = "registry-fixture"
      "app.kubernetes.io/part-of"  = "registry-stack"
      environment                 = "staging"
    })
    error_message = "The real provider must preserve registry Namespace label strings."
  }
}

run "empty_label_string_is_not_null" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  variables {
    part_of = ""
  }

  assert {
    condition     = kubernetes_namespace_v1.this.metadata[0].labels["app.kubernetes.io/part-of"] == ""
    error_message = "An empty label string is valid and must not be confused with null."
  }
}

run "reject_null_environment" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  variables {
    environment = null
  }

  expect_failures = [var.environment]
}

run "reject_null_namespace" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  variables {
    namespace = null
  }

  expect_failures = [var.namespace]
}

run "reject_null_namespace_app_name" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  variables {
    namespace_app_name = null
  }

  expect_failures = [var.namespace_app_name]
}

run "reject_null_part_of" {
  command = plan

  module {
    source = "../../modules/homelab_registry"
  }

  plan_options {
    refresh = false
    target  = [kubernetes_namespace_v1.this]
  }

  variables {
    part_of = null
  }

  expect_failures = [var.part_of]
}
