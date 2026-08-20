# Azure Database for PostgreSQL Flexible Server, private-only. The VM has no
# public IP, so the database must be reachable inside the VNet. This uses
# Flexible Server's delegated-subnet VNet integration.
#
# It used to use a private endpoint in the app's own 10.20.1.0/24 instead,
# because VNet integration needs a subnet dedicated to the database and that
# one already holds the VM. The endpoint billed about CA$7/month for
# reachability that integration provides for free, so the database now gets its
# own subnet below. Switching modes is not an in-place operation: Flexible
# Server fixes its networking at creation, so this change replaces the server.

# VNet integration requires a subnet delegated to the database service and used
# by nothing else. 10.20.2.0/24 is free space in the VNet's 10.20.0.0/16.
resource "azurerm_subnet" "postgres" {
  name                 = "${local.name}-pg-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.2.0/24"]
  # Flexible Server adds this endpoint to its own delegated subnet at creation.
  # Pinning the assigned value keeps it out of every subsequent plan as a
  # phantom "1 to change"; without it Terraform perpetually tries to strip it.
  service_endpoints = ["Microsoft.Storage"]

  delegation {
    name = "postgres"

    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Private DNS resolution for the server's FQDN inside the VNet.
#
# This name is not a free choice, and it is deliberately not the "privatelink."
# zone a private endpoint would use: delegated-subnet integration requires a
# zone whose name ends in ".private.postgres.database.azure.com".
#
# It does not change how the app connects. Azure registers the server in this
# zone under a *generated* label (not the server name) and points the server's
# ordinary public FQDN at it through a CNAME chain, so inside the VNet
# "<server>-postgres.postgres.database.azure.com" still resolves -- now to the
# private 10.20.2.0/24 address. DATABASE_URL is therefore identical in
# private-endpoint and VNet-integrated modes, which is why the 2026-08-20
# migration needed no change to /etc/hangout-automator.env.
resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.name}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "${local.name}-pg-dns-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = "${local.name}-postgres"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  version             = "17"
  # Burstable B1ms is the cheapest tier that comfortably serves a single app
  # with one worker; 32 GB storage leaves headroom for audit traffic.
  sku_name              = "B_Standard_B1ms"
  storage_mb            = 32768
  backup_retention_days = 35
  # Azure assigns an availability zone at creation whether or not one is asked
  # for. Pinning the assigned value keeps it out of every subsequent plan as a
  # phantom "1 to change"; without it Terraform perpetually tries to null it.
  zone = "1"
  # No default: sourced from POSTGRES_ADMIN_PASSWORD in the ignored .env.
  administrator_login    = var.postgres_admin_user
  administrator_password = var.postgres_admin_password
  # Private-only. VNet integration has no public endpoint at all, so there is
  # no firewall rule surface to get wrong.
  public_network_access_enabled = false
  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id

  # The zone must be linked to the VNet before the server registers into it.
  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "hangout"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
