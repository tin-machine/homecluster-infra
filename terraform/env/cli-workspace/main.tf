terraform {
  required_version = ">= 1.7.0"
  backend "local" {}
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
  }
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

module "workspace" {
  source                          = "../../modules/cli_workspace"
  namespace                       = var.namespace
  name                            = var.name
  image                           = var.image
  node_name                       = var.node_name
  nas_server                      = var.nas_server
  nas_export_path                 = var.nas_export_path
  nas_home_subpath                = var.nas_home_subpath
  home_mount_path                 = var.home_mount_path
  user_name                       = var.user_name
  user_id                         = var.user_id
  group_id                        = var.group_id
  state_storage_size              = var.state_storage_size
  ssh_enabled                     = var.ssh_enabled
  ssh_image                       = var.ssh_image
  ssh_authorized_keys_secret_name = var.ssh_authorized_keys_secret_name
  ssh_tailnet_hostname            = var.ssh_tailnet_hostname
  environment                     = var.environment
  replicas                        = var.replicas
  ssh_tailnet_tags                = var.ssh_tailnet_tags
  cpu_limit                       = var.cpu_limit
  memory_limit                    = var.memory_limit
  nas_mount_options               = var.nas_mount_options
  runtime_class_name              = var.runtime_class_name
}
