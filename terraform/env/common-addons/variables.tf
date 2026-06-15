variable "metallb_namespace" {
  type        = string
  description = "Namespace where MetalLB is installed by terraform/env/common-crds"
  default     = "metallb-system"
}

variable "metallb_address_pool" {
  type        = list(string)
  description = "MetalLB L2 address pool (CIDRs or start-end)"
}

variable "metallb_bgp_address_pool" {
  type        = list(string)
  description = "MetalLB BGP address pool (CIDRs or start-end)"
}

variable "metallb_bgp_peer_address" {
  type        = string
  description = "OpenWrt FRR peer address for MetalLB BGP session"
}

variable "metallb_bgp_peer_asn" {
  type        = number
  description = "BGP ASN advertised by the OpenWrt peer"
}

variable "metallb_bgp_my_asn" {
  type        = number
  description = "BGP ASN used by the MetalLB speakers"
}

variable "nfs_server" {
  type        = string
  description = "NAS IP for NFS"
}

variable "nfs_path" {
  type        = string
  description = "Exported NFS path for dynamic PVs"
}

variable "ingress_nginx_load_balancer_ip" {
  type        = string
  description = "Static IP assigned to ingress-nginx LoadBalancer from the MetalLB BGP pool"
}

variable "addon_node_selector" {
  type        = map(string)
  description = "Optional nodeSelector applied to common add-ons. Use this to keep heavy runtime image unpack off the control-plane node."
}
