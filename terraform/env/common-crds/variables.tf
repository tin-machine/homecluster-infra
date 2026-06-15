variable "metallb_address_pool" {
  type        = list(string)
  description = "MetalLB L2 address pool (CIDRs or start-end)"
}

variable "addon_node_selector" {
  type        = map(string)
  description = "Optional nodeSelector applied to common add-ons. Use this to keep heavy runtime image unpack off the control-plane node."
}

variable "metallb_tolerate_control_plane" {
  type        = bool
  description = "Whether MetalLB controller/speaker should tolerate control-plane NoSchedule taints."
}
