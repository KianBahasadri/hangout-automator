# Azure Database for PostgreSQL Flexible Server, private-only. The VM has no
# public IP, so the database must be reachable inside the VNet: a private
# endpoint in the existing 10.20.1.0/24 subnet plus a private DNS zone.
# (Flexible Server's delegated-subnet VNet integration needs an *empty*
# subnet; the app VM already occupies this one, so a private endpoint is the
# right mechanism here.)

# Private DNS resolution for the server's FQDN inside the VNet.
#
# This name is not a free choice. Azure resolves the server's public FQDN to a
# CNAME at <server>.privatelink.postgres.database.azure.com, and the private
# endpoint's zone group registers its A record there. Any other zone name (for
# example "private.", which is the convention for the *delegated-subnet* VNet
# integration mode this deployment does not use) leaves the CNAME target
# unresolvable inside the VNet, so lookups fall through to public DNS and
# return the public IP — which then refuses the connection, because
# public_network_access_enabled is false below.
resource "azurerm_private_dns_zone" "postgres" {
  name                = "privatelink.postgres.database.azure.com"
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
  # Private-only: no public endpoint, no "allow all" firewall rules. Only the
  # private endpoint inside the VNet can reach it.
  public_network_access_enabled = false

  lifecycle {
    # The database is the source of truth after migration; Terraform must
    # never replace or delete it. Remove this only after deliberately
    # standing up a replacement and migrating data.
    prevent_destroy = true
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "hangout"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_private_endpoint" "postgres" {
  name                = "${local.name}-pg-endpoint"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.main.id

  private_service_connection {
    name                           = "${local.name}-pg-conn"
    private_connection_resource_id = azurerm_postgresql_flexible_server.main.id
    is_manual_connection           = false
    subresource_names              = ["postgresqlServer"]
  }

  private_dns_zone_group {
    name                 = "postgres"
    private_dns_zone_ids = [azurerm_private_dns_zone.postgres.id]
  }
}
