data "external" "portal" {
  program = ["python3", "${path.module}/../../../scripts/prod_monitoring_portal_render.py"]
  query = {
    portal_site_path = var.portal_site_path
  }
}

data "kubernetes_namespace_v1" "cluster_identity" {
  metadata { name = "kube-system" }
}

locals {
  rendered = jsondecode(data.external.portal.result.manifest)
  resources = {
    for resource in local.rendered.items :
    "${resource.kind}/${resource.metadata.name}" => resource
    if var.enable_tailnet || (
      resource.kind != "Ingress" && resource.metadata.name != "prod-grafana-tailnet-ingress"
    )
  }
}

resource "helm_release" "tailscale_operator" {
  count            = var.enable_tailnet ? 1 : 0
  name             = "tailscale-operator"
  repository       = "https://pkgs.tailscale.com/helmcharts"
  chart            = "tailscale-operator"
  version          = "1.104.2"
  namespace        = "tailscale"
  create_namespace = true
  wait             = true
  timeout          = 600

  # The stable chart is 1.104.2; pin its Serve proxy to the patched image.
  # OAuth is supplied only through the existing tailscale/operator-oauth Secret.
  values = [yamlencode({
    proxyConfig = {
      image = { tag = "v1.104.4" }
    }
  })]

  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
  }
}

resource "kubernetes_manifest" "portal" {
  for_each = { for key, resource in local.resources : key => resource if resource.kind != "Deployment" }
  manifest = each.value

  depends_on = [helm_release.tailscale_operator]

  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
  }
}

resource "kubernetes_manifest" "portal_deployment" {
  for_each = { for key, resource in local.resources : key => resource if resource.kind == "Deployment" }
  manifest = each.value

  depends_on = [kubernetes_manifest.portal]
  wait {
    rollout = true
  }
  timeouts {
    create = "5m"
    update = "5m"
  }

  lifecycle {
    precondition {
      condition     = data.kubernetes_namespace_v1.cluster_identity.metadata[0].uid == var.expected_kube_system_uid
      error_message = "Production cluster identity does not match the expected kube-system UID."
    }
  }
}
