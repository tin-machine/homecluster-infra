terraform {
  required_version = ">= 1.7.0"

  backend "local" {}

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.25.0"
    }
  }
}

provider "cloudflare" {}
