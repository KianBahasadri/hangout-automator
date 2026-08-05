data "azurerm_client_config" "current" {}

locals {
  state_resource_group = "${var.prefix}-tfstate-rg"
  storage_suffix       = substr(replace(var.subscription_id, "-", ""), 0, 8)
  storage_account_name = substr("${var.prefix}state${local.storage_suffix}", 0, 24)
  operator_object_id   = var.operator_object_id != "" ? var.operator_object_id : data.azurerm_client_config.current.object_id
}

resource "azurerm_resource_group" "state" {
  name     = local.state_resource_group
  location = var.location
}

resource "azurerm_storage_account" "state" {
  name                            = local.storage_account_name
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  public_network_access_enabled   = true
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  depends_on            = [azurerm_role_assignment.state]
}

resource "azurerm_role_assignment" "state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.operator_object_id
}
