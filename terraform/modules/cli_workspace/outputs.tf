output "placement" {
  description = "Non-secret workspace placement and resource budget."
  value = {
    namespace          = kubernetes_namespace_v1.this.metadata[0].name
    deployment         = kubernetes_deployment_v1.this.metadata[0].name
    environment        = kubernetes_deployment_v1.this.metadata[0].labels.environment
    replicas           = kubernetes_deployment_v1.this.spec[0].replicas
    node_name          = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].node_selector["kubernetes.io/hostname"]
    runtime_class_name = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].runtime_class_name
    limits             = kubernetes_deployment_v1.this.spec[0].template[0].spec[0].container[0].resources[0].limits
    nas_mount_options  = try(kubernetes_persistent_volume_v1.home[0].spec[0].mount_options, [])
    tailnet_tags       = try(kubernetes_service_v1.ssh[0].metadata[0].annotations["tailscale.com/tags"], null)
  }
}
