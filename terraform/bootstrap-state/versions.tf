terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# The state account sets shared_access_key_enabled = false, so every data-plane
# call the provider makes — the post-create blob service probe and the container
# create — has to use an Azure AD token. Without this the account is created and
# then immediately fails with "Key based authentication is not permitted".
provider "azurerm" {
  subscription_id     = var.subscription_id
  storage_use_azuread = true
  features {}
}
