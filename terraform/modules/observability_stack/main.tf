locals {
  minio_release              = "${var.release_prefix}-minio"
  loki_release               = "${var.release_prefix}-loki"
  tempo_release              = "${var.release_prefix}-tempo"
  prometheus_release         = "${var.release_prefix}-prometheus"
  node_exporter_release      = "${var.release_prefix}-node-exporter"
  kube_state_metrics_release = "${var.release_prefix}-kube-state-metrics"
  mimir_release              = "${var.release_prefix}-mimir"
  grafana_release            = "${var.release_prefix}-grafana"
  otel_collector_release     = "${var.release_prefix}-otel-collector"

  loki_objectstore_access_key = coalesce(var.loki_objectstore_access_key, var.minio_root_user)
  loki_objectstore_secret_key = coalesce(var.loki_objectstore_secret_key, var.minio_root_password)
}

resource "kubernetes_namespace" "object_storage" {
  metadata {
    name = var.object_storage_namespace
    labels = {
      "app.kubernetes.io/name"     = "object-storage"
      "app.kubernetes.io/instance" = var.object_storage_namespace
      "app.kubernetes.io/part-of"  = "observability-stack"
      environment                  = var.environment
    }
  }
}

resource "helm_release" "minio" {
  name             = local.minio_release
  repository       = "https://charts.min.io/"
  chart            = "minio"
  version          = "5.4.0"
  namespace        = kubernetes_namespace.object_storage.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-minio.yaml"),
    file("${var.values_env_dir}/values-minio.yaml"),
    file("${var.values_site_dir}/values-minio.yaml")
  ]

  depends_on = [kubernetes_namespace.object_storage]

  set_sensitive {
    name  = "rootUser"
    value = var.minio_root_user
  }

  set_sensitive {
    name  = "rootPassword"
    value = var.minio_root_password
  }
}

resource "kubernetes_namespace" "observability" {
  metadata {
    name = var.observability_namespace
    labels = {
      "app.kubernetes.io/name"     = "observability"
      "app.kubernetes.io/instance" = var.observability_namespace
      "app.kubernetes.io/part-of"  = "observability-stack"
      environment                  = var.environment
    }
  }
}

resource "helm_release" "loki" {
  name             = local.loki_release
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "loki"
  version          = "5.45.0"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-loki.yaml"),
    file("${var.values_env_dir}/values-loki.yaml"),
    file("${var.values_site_dir}/values-loki.yaml")
  ]

  depends_on = [
    kubernetes_namespace.observability,
    helm_release.minio
  ]

  set_sensitive {
    name  = "loki.storage_config.aws.access_key_id"
    value = local.loki_objectstore_access_key
  }

  set_sensitive {
    name  = "loki.storage_config.aws.secret_access_key"
    value = local.loki_objectstore_secret_key
  }

  set_sensitive {
    name  = "loki.rulerConfig.storage.s3.access_key_id"
    value = local.loki_objectstore_access_key
  }

  set_sensitive {
    name  = "loki.rulerConfig.storage.s3.secret_access_key"
    value = local.loki_objectstore_secret_key
  }
}

resource "helm_release" "tempo" {
  name             = local.tempo_release
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "tempo"
  version          = "1.23.3"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-tempo.yaml"),
    file("${var.values_env_dir}/values-tempo.yaml"),
    file("${var.values_site_dir}/values-tempo.yaml")
  ]

  depends_on = [kubernetes_namespace.observability]
}

resource "helm_release" "prometheus" {
  name             = local.prometheus_release
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "prometheus"
  version          = "25.8.2"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-prometheus.yaml"),
    file("${var.values_env_dir}/values-prometheus.yaml"),
    file("${var.values_site_dir}/values-prometheus.yaml")
  ]

  depends_on = [kubernetes_namespace.observability]
}

resource "helm_release" "node_exporter" {
  name             = local.node_exporter_release
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "prometheus-node-exporter"
  version          = "4.47.3"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-node-exporter.yaml"),
    file("${var.values_env_dir}/values-node-exporter.yaml"),
    file("${var.values_site_dir}/values-node-exporter.yaml")
  ]

  depends_on = [kubernetes_namespace.observability]
}

resource "helm_release" "kube_state_metrics" {
  name             = local.kube_state_metrics_release
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-state-metrics"
  version          = "6.3.0"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-kube-state-metrics.yaml"),
    file("${var.values_env_dir}/values-kube-state-metrics.yaml"),
    file("${var.values_site_dir}/values-kube-state-metrics.yaml")
  ]

  depends_on = [kubernetes_namespace.observability]
}

resource "helm_release" "mimir" {
  name             = local.mimir_release
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "mimir-distributed"
  version          = "5.8.0"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-mimir.yaml"),
    file("${var.values_env_dir}/values-mimir.yaml"),
    file("${var.values_site_dir}/values-mimir.yaml")
  ]

  depends_on = [
    kubernetes_namespace.observability,
    helm_release.minio
  ]

  set {
    name  = "mimir.structuredConfig.blocks_storage.s3.endpoint"
    value = "${local.minio_release}.${var.object_storage_namespace}.svc.cluster.local:9000"
  }

  set {
    name  = "mimir.structuredConfig.ruler_storage.s3.endpoint"
    value = "${local.minio_release}.${var.object_storage_namespace}.svc.cluster.local:9000"
  }

  set {
    name  = "mimir.structuredConfig.alertmanager_storage.s3.endpoint"
    value = "${local.minio_release}.${var.object_storage_namespace}.svc.cluster.local:9000"
  }

  set_sensitive {
    name  = "mimir.structuredConfig.blocks_storage.s3.access_key_id"
    value = var.minio_root_user
  }

  set_sensitive {
    name  = "mimir.structuredConfig.blocks_storage.s3.secret_access_key"
    value = var.minio_root_password
  }

  set_sensitive {
    name  = "mimir.structuredConfig.ruler_storage.s3.access_key_id"
    value = var.minio_root_user
  }

  set_sensitive {
    name  = "mimir.structuredConfig.ruler_storage.s3.secret_access_key"
    value = var.minio_root_password
  }

  set_sensitive {
    name  = "mimir.structuredConfig.alertmanager_storage.s3.access_key_id"
    value = var.minio_root_user
  }

  set_sensitive {
    name  = "mimir.structuredConfig.alertmanager_storage.s3.secret_access_key"
    value = var.minio_root_password
  }
}

resource "kubernetes_config_map" "grafana_dashboards" {
  for_each = { for path in var.grafana_dashboard_files : basename(path) => path }

  metadata {
    name      = substr(replace(lower(each.key), ".json", ""), 0, 63)
    namespace = kubernetes_namespace.observability.metadata[0].name
    labels = {
      grafana_dashboard           = "observability"
      "app.kubernetes.io/part-of" = "observability"
      environment                 = var.environment
    }
  }

  data = {
    basename(each.value) = file("${var.repo_root}/${each.value}")
  }

  depends_on = [kubernetes_namespace.observability]
}

resource "kubernetes_secret" "grafana_admin" {
  count = var.grafana_admin_user != null && var.grafana_admin_password != null ? 1 : 0

  metadata {
    name      = "${local.grafana_release}-admin"
    namespace = kubernetes_namespace.observability.metadata[0].name
    labels = {
      "app.kubernetes.io/name"     = "grafana"
      "app.kubernetes.io/instance" = local.grafana_release
      "app.kubernetes.io/part-of"  = "observability-stack"
      environment                  = var.environment
    }
  }

  data_wo = {
    admin-user     = var.grafana_admin_user
    admin-password = var.grafana_admin_password
  }
  data_wo_revision = var.grafana_admin_secret_revision

  type = "Opaque"

  depends_on = [kubernetes_namespace.observability]
}

resource "helm_release" "grafana" {
  name             = local.grafana_release
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "grafana"
  version          = "8.6.2"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false
  timeout          = 900

  values = [
    file("${var.values_base_dir}/values-grafana.yaml"),
    file("${var.values_env_dir}/values-grafana.yaml"),
    file("${var.values_site_dir}/values-grafana.yaml")
  ]

  set {
    name  = "service.loadBalancerIP"
    value = var.grafana_load_balancer_ip
  }

  depends_on = [
    helm_release.prometheus,
    helm_release.loki,
    helm_release.tempo,
    helm_release.mimir,
    kubernetes_secret.grafana_admin
  ]
}

resource "helm_release" "otel_collector" {
  name             = local.otel_collector_release
  repository       = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart            = "opentelemetry-collector"
  version          = "0.93.1"
  namespace        = kubernetes_namespace.observability.metadata[0].name
  create_namespace = false

  values = [
    file("${var.values_base_dir}/values-otel-collector.yaml"),
    file("${var.values_env_dir}/values-otel-collector.yaml"),
    file("${var.values_site_dir}/values-otel-collector.yaml")
  ]

  depends_on = [
    helm_release.loki,
    helm_release.tempo,
    helm_release.prometheus,
    helm_release.mimir,
    kubernetes_namespace.observability
  ]
}

resource "kubernetes_cron_job_v1" "trace_smoke_producer" {
  metadata {
    name      = "${var.release_prefix}-trace-smoke-producer"
    namespace = kubernetes_namespace.observability.metadata[0].name
    labels = {
      "app.kubernetes.io/name"      = "trace-smoke-producer"
      "app.kubernetes.io/instance"  = "${var.release_prefix}-trace-smoke-producer"
      "app.kubernetes.io/component" = "traces"
      "app.kubernetes.io/part-of"   = "observability-stack"
      environment                   = var.environment
    }
  }

  spec {
    schedule                      = "*/5 * * * *"
    concurrency_policy            = "Forbid"
    successful_jobs_history_limit = 1
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = {
          "app.kubernetes.io/name"      = "trace-smoke-producer"
          "app.kubernetes.io/instance"  = "${var.release_prefix}-trace-smoke-producer"
          "app.kubernetes.io/component" = "traces"
          "app.kubernetes.io/part-of"   = "observability-stack"
          environment                   = var.environment
        }
      }

      spec {
        active_deadline_seconds    = 60
        backoff_limit              = 1
        ttl_seconds_after_finished = 600

        template {
          metadata {
            labels = {
              "app.kubernetes.io/name"      = "trace-smoke-producer"
              "app.kubernetes.io/instance"  = "${var.release_prefix}-trace-smoke-producer"
              "app.kubernetes.io/component" = "traces"
              "app.kubernetes.io/part-of"   = "observability-stack"
              environment                   = var.environment
            }
          }

          spec {
            automount_service_account_token = false
            node_selector                   = var.trace_smoke_node_selector
            restart_policy                  = "Never"

            container {
              name              = "trace-smoke-producer"
              image             = "curlimages/curl:8.10.1"
              image_pull_policy = "IfNotPresent"
              command           = ["/bin/sh", "-ec"]
              args = [<<-EOT
                seconds="$(date +%s)"
                now="$((seconds * 1000000000))"
                start="$((now - 1000000000))"
                trace_id="$(printf '0000000000000000%016x' "$seconds")"
                span_id="$(printf '%016x' "$seconds")"

                cat <<JSON | curl --connect-timeout 5 --max-time 15 -fsS -H 'Content-Type: application/json' --data-binary @- "$OTLP_HTTP_ENDPOINT"
                {"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"$SERVICE_NAME"}},{"key":"service.namespace","value":{"stringValue":"observability"}},{"key":"environment","value":{"stringValue":"$ENVIRONMENT"}}]},"scopeSpans":[{"scope":{"name":"terraform-observability-trace-smoke"},"spans":[{"traceId":"$trace_id","spanId":"$span_id","name":"observability-smoke","kind":"SPAN_KIND_INTERNAL","startTimeUnixNano":"$start","endTimeUnixNano":"$now","attributes":[{"key":"smoke.producer","value":{"stringValue":"kubernetes-cronjob"}},{"key":"tenant.id","value":{"stringValue":"$TENANT_ID"}}],"status":{"code":"STATUS_CODE_OK"}}]}]}]}
                JSON
              EOT
              ]

              env {
                name  = "OTLP_HTTP_ENDPOINT"
                value = "http://${local.otel_collector_release}-opentelemetry-collector.${var.observability_namespace}.svc.cluster.local:4318/v1/traces"
              }

              env {
                name  = "SERVICE_NAME"
                value = "${var.release_prefix}-observability-smoke"
              }

              env {
                name  = "ENVIRONMENT"
                value = var.environment
              }

              env {
                name  = "TENANT_ID"
                value = var.tenant_id
              }

              resources {
                requests = {
                  cpu    = "5m"
                  memory = "16Mi"
                }
                limits = {
                  cpu    = "50m"
                  memory = "64Mi"
                }
              }

              security_context {
                allow_privilege_escalation = false
                read_only_root_filesystem  = true
                run_as_group               = 65532
                run_as_non_root            = true
                run_as_user                = 65532

                capabilities {
                  drop = ["ALL"]
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    helm_release.otel_collector,
    helm_release.tempo
  ]
}

resource "kubernetes_service" "otel_collector_syslog" {
  metadata {
    name      = "${local.otel_collector_release}-syslog"
    namespace = kubernetes_namespace.observability.metadata[0].name
    labels = {
      "app.kubernetes.io/name"     = "opentelemetry-collector"
      "app.kubernetes.io/instance" = helm_release.otel_collector.name
      "app.kubernetes.io/part-of"  = "observability"
      environment                  = var.environment
    }
  }

  spec {
    selector = {
      "app.kubernetes.io/name"     = "opentelemetry-collector"
      "app.kubernetes.io/instance" = helm_release.otel_collector.name
    }

    type                        = "LoadBalancer"
    load_balancer_ip            = var.otel_syslog_load_balancer_ip
    load_balancer_source_ranges = var.otel_syslog_load_balancer_source_ranges
    external_traffic_policy     = "Local"

    port {
      name        = "syslog-udp"
      port        = 1514
      protocol    = "UDP"
      target_port = 1514
    }

    port {
      name        = "syslog-tcp"
      port        = 6514
      protocol    = "TCP"
      target_port = 6514
    }
  }

  lifecycle {
    ignore_changes = [
      metadata[0].annotations["metallb.universe.tf/ip-allocated-from-pool"],
    ]
  }

  depends_on = [helm_release.otel_collector]
}

resource "kubernetes_cluster_role" "otel_collector" {
  metadata {
    name = local.otel_collector_release
    labels = {
      "app.kubernetes.io/name"     = "opentelemetry-collector"
      "app.kubernetes.io/instance" = local.otel_collector_release
      "app.kubernetes.io/part-of"  = "observability"
      environment                  = var.environment
    }
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "services", "endpoints", "nodes"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["discovery.k8s.io"]
    resources  = ["endpointslices"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding" "otel_collector" {
  metadata {
    name = local.otel_collector_release
    labels = {
      "app.kubernetes.io/name"     = "opentelemetry-collector"
      "app.kubernetes.io/instance" = local.otel_collector_release
      "app.kubernetes.io/part-of"  = "observability"
      environment                  = var.environment
    }
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.otel_collector.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = "${local.otel_collector_release}-opentelemetry-collector"
    namespace = kubernetes_namespace.observability.metadata[0].name
  }

  depends_on = [
    kubernetes_cluster_role.otel_collector,
    helm_release.otel_collector
  ]
}
