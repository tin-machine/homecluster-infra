resource "kubernetes_namespace" "ingress" {
  metadata {
    name = "ingress-nginx"
    labels = {
      "app.kubernetes.io/name"     = "ingress-nginx"
      "app.kubernetes.io/instance" = "shared-ingress"
      "app.kubernetes.io/part-of"  = "networking-stack"
      environment                  = "shared"
    }
  }
}

resource "kubernetes_namespace" "storage" {
  metadata {
    name = "storage"
    labels = {
      "app.kubernetes.io/name"     = "nfs-provisioner"
      "app.kubernetes.io/instance" = "shared-storage"
      "app.kubernetes.io/part-of"  = "storage-stack"
      environment                  = "shared"
    }
  }
}

resource "kubernetes_manifest" "metallb_pool" {
  manifest = {
    apiVersion = "metallb.io/v1beta1"
    kind       = "IPAddressPool"
    metadata = {
      name      = "lan-pool"
      namespace = var.metallb_namespace
    }
    spec = {
      addresses = var.metallb_address_pool
    }
  }
}

resource "kubernetes_manifest" "metallb_l2adv" {
  manifest = {
    apiVersion = "metallb.io/v1beta1"
    kind       = "L2Advertisement"
    metadata = {
      name      = "lan-adv"
      namespace = var.metallb_namespace
    }
    spec = {
      ipAddressPools = ["lan-pool"]
      interfaces     = ["wlan0"]
    }
  }

  depends_on = [kubernetes_manifest.metallb_pool]
}

resource "kubernetes_manifest" "metallb_bgp_pool" {
  manifest = {
    apiVersion = "metallb.io/v1beta1"
    kind       = "IPAddressPool"
    metadata = {
      name      = "vip-bgp-pool"
      namespace = var.metallb_namespace
    }
    spec = {
      addresses = var.metallb_bgp_address_pool
    }
  }
}

resource "kubernetes_manifest" "metallb_bgp_peer_openwrt" {
  manifest = {
    apiVersion = "metallb.io/v1beta2"
    kind       = "BGPPeer"
    metadata = {
      name      = "openwrt"
      namespace = var.metallb_namespace
    }
    spec = {
      myASN        = var.metallb_bgp_my_asn
      peerASN      = var.metallb_bgp_peer_asn
      peerAddress  = var.metallb_bgp_peer_address
      holdTime     = "180s"
      ebgpMultiHop = false
    }
  }
}

resource "kubernetes_manifest" "metallb_bgp_adv_bgp_pool" {
  manifest = {
    apiVersion = "metallb.io/v1beta1"
    kind       = "BGPAdvertisement"
    metadata = {
      name      = "vip-bgp-adv"
      namespace = var.metallb_namespace
    }
    spec = {
      ipAddressPools    = ["vip-bgp-pool"]
      aggregationLength = 32
      localPref         = 100
    }
  }

  depends_on = [kubernetes_manifest.metallb_bgp_pool, kubernetes_manifest.metallb_bgp_peer_openwrt]
}

resource "helm_release" "ingress_nginx" {
  name       = "ingress-nginx"
  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  namespace  = kubernetes_namespace.ingress.metadata[0].name
  version    = "4.11.3"
  timeout    = 900
  wait       = true

  values = [yamlencode({
    controller = {
      kind         = "DaemonSet"
      nodeSelector = var.addon_node_selector
      ingressClassResource = {
        name            = "nginx"
        controllerValue = "k8s.io/ingress-nginx"
        default         = true
      }
      service = {
        type = "LoadBalancer"
        annotations = {
          "metallb.universe.tf/address-pool" = "vip-bgp-pool"
        }
        loadBalancerIP        = var.ingress_nginx_load_balancer_ip
        externalTrafficPolicy = "Local"
      }
      config = {
        use-forwarded-headers = "true"
        proxy-body-size       = "32m"
      }
    }
  })]

  depends_on = [kubernetes_manifest.metallb_bgp_adv_bgp_pool]
}

resource "helm_release" "nfs_provisioner" {
  name       = "nfs-subdir-external-provisioner"
  repository = "https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner"
  chart      = "nfs-subdir-external-provisioner"
  namespace  = kubernetes_namespace.storage.metadata[0].name
  version    = "4.0.18"

  values = [yamlencode({
    nodeSelector = var.addon_node_selector
    nfs = {
      server = var.nfs_server
      path   = var.nfs_path
    }
    storageClass = {
      name            = "nfs-client"
      defaultClass    = true
      reclaimPolicy   = "Retain"
      archiveOnDelete = true
      onDelete        = "retain"
    }
    mountOptions = [
      "vers=3",
      "rsize=1048576",
      "wsize=1048576",
      "hard",
      "timeo=600",
      "retrans=2",
    ]
  })]
}
