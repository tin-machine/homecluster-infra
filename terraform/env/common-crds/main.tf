resource "kubernetes_namespace" "metallb" {
  metadata {
    name = "metallb-system"
    labels = {
      "app.kubernetes.io/name"     = "metallb"
      "app.kubernetes.io/instance" = "shared-metallb"
      "app.kubernetes.io/part-of"  = "networking-stack"
      environment                  = "shared"
    }
  }
}

resource "kubernetes_namespace" "cert_manager" {
  metadata {
    name = "cert-manager"
    labels = {
      "app.kubernetes.io/name"     = "cert-manager"
      "app.kubernetes.io/instance" = "shared-cert-manager"
      "app.kubernetes.io/part-of"  = "security-stack"
      environment                  = "shared"
    }
  }
}

resource "helm_release" "metallb" {
  name       = "metallb"
  repository = "https://metallb.github.io/metallb"
  chart      = "metallb"
  namespace  = kubernetes_namespace.metallb.metadata[0].name
  version    = "0.14.5"

  set {
    name  = "crds.enabled"
    value = "true"
  }

  values = [yamlencode({
    controller = {
      nodeSelector = var.addon_node_selector
      livenessProbe = {
        initialDelaySeconds = 120
        periodSeconds       = 10
        failureThreshold    = 6
      }
      readinessProbe = {
        initialDelaySeconds = 30
        periodSeconds       = 10
        failureThreshold    = 6
      }
      tolerations = var.metallb_tolerate_control_plane ? [{
        key      = "node-role.kubernetes.io/control-plane"
        operator = "Exists"
        effect   = "NoSchedule"
      }] : []
    }
    speaker = {
      nodeSelector = var.addon_node_selector
      tolerations = var.metallb_tolerate_control_plane ? [{
        key      = "node-role.kubernetes.io/control-plane"
        operator = "Exists"
        effect   = "NoSchedule"
      }] : []
      frr = {
        enabled = true
      }
    }
    ipAddressPools = [{
      name      = "lan-pool"
      addresses = var.metallb_address_pool
    }]
    l2Advertisements = [{
      ipAddressPools = ["lan-pool"]
    }]
  })]
}

resource "helm_release" "cert_manager" {
  name       = "cert-manager"
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  namespace  = kubernetes_namespace.cert_manager.metadata[0].name
  version    = "1.20.2"
  timeout    = 600
  wait       = true

  values = [yamlencode({
    crds = {
      enabled = true
    }
    global = {
      commonLabels = {
        "app.kubernetes.io/part-of" = "security-stack"
        environment                 = "shared"
      }
    }
    nodeSelector = var.addon_node_selector
    cainjector = {
      nodeSelector = var.addon_node_selector
    }
    webhook = {
      nodeSelector = var.addon_node_selector
    }
    startupapicheck = {
      nodeSelector = var.addon_node_selector
    }
  })]
}
