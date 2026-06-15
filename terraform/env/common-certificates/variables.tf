variable "cert_manager_namespace" {
  type        = string
  description = "Namespace where cert-manager is installed"
  default     = "cert-manager"
}

variable "internal_ca_secret_name" {
  type        = string
  description = "Secret name containing the internal CA key pair for cert-manager CA issuer"
}

variable "internal_ca_cluster_issuer_name" {
  type        = string
  description = "ClusterIssuer name for the home-lab internal CA"
}

variable "internal_ca_app_name" {
  type        = string
  description = "app.kubernetes.io/name label for the internal CA resources"
}

variable "internal_ca_tls_crt" {
  type        = string
  description = "PEM encoded internal CA certificate. Inject from a root-only tfvars file or SOPS; do not commit it."
  sensitive   = true
}

variable "internal_ca_tls_key" {
  type        = string
  description = "PEM encoded internal CA private key. Inject from a root-only tfvars file or SOPS; do not commit it."
  sensitive   = true
}

variable "internal_ca_revision" {
  type        = number
  description = "Revision marker for the write-only internal CA Secret data. Increment when rotating the CA."
}
