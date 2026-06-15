terraform {
  required_version = ">= 1.7.0"

  backend "local" {}

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.33"
    }
  }
}

variable "kubeconfig_path" {
  type        = string
  description = "Path to kubeconfig for the k3s cluster"
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}
