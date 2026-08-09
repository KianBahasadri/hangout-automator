locals {
  name = var.prefix
}

resource "azurerm_resource_group" "main" {
  name     = "${local.name}-rg"
  location = var.location
}

resource "azurerm_virtual_network" "main" {
  name                = "${local.name}-vnet"
  address_space       = ["10.20.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_subnet" "main" {
  name                            = "${local.name}-subnet"
  resource_group_name             = azurerm_resource_group.main.name
  virtual_network_name            = azurerm_virtual_network.main.name
  address_prefixes                = ["10.20.1.0/24"]
  default_outbound_access_enabled = false
}

# New Azure subnets do not receive implicit outbound internet access. A NAT
# Gateway provides egress for apt, git, pip, and cloudflared without assigning
# a public IP to the VM or allowing inbound connections.
resource "azurerm_public_ip" "nat" {
  name                = "${local.name}-nat-pip"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_nat_gateway" "main" {
  name                    = "${local.name}-nat"
  location                = azurerm_resource_group.main.location
  resource_group_name     = azurerm_resource_group.main.name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
}

resource "azurerm_nat_gateway_public_ip_association" "main" {
  nat_gateway_id       = azurerm_nat_gateway.main.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_subnet_nat_gateway_association" "main" {
  subnet_id      = azurerm_subnet.main.id
  nat_gateway_id = azurerm_nat_gateway.main.id
}

# The VM has no public IP and the NSG intentionally has no inbound allow rules.
# cloudflared establishes the only public path through an outbound tunnel.
resource "azurerm_network_security_group" "main" {
  name                = "${local.name}-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_network_interface" "main" {
  name                = "${local.name}-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.main.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface_security_group_association" "main" {
  network_interface_id      = azurerm_network_interface.main.id
  network_security_group_id = azurerm_network_security_group.main.id
}

resource "azurerm_managed_disk" "data" {
  name                 = "${local.name}-data-disk"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  storage_account_type = "StandardSSD_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.data_disk_size_gb

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_linux_virtual_machine" "main" {
  name                = "${local.name}-vm"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  size                = var.vm_size
  admin_username      = var.admin_username

  # Ensure cloud-init has NAT-backed egress before it tries apt, git, or pip.
  depends_on = [azurerm_subnet_nat_gateway_association.main]

  network_interface_ids = [
    azurerm_network_interface.main.id,
  ]

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  boot_diagnostics {}

  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    admin_username           = var.admin_username
    git_repo_url             = var.git_repo_url
    git_branch               = var.git_branch
    git_revision             = var.git_revision
    sms_provider             = var.sms_provider
    twilio_account_sid       = var.twilio_account_sid
    twilio_auth_token        = var.twilio_auth_token
    twilio_from_number       = var.twilio_from_number
    google_maps_api_key      = var.google_maps_api_key
    clerk_enabled            = var.clerk_enabled
    clerk_publishable_key    = var.clerk_publishable_key
    clerk_frontend_api_url   = var.clerk_frontend_api_url
    clerk_secret_key         = var.clerk_secret_key
    clerk_jwt_key            = var.clerk_jwt_key
    clerk_authorized_parties = var.clerk_authorized_parties
    access_bootstrap_admins  = var.access_bootstrap_admins
    followup_hours           = var.followup_hours
    organizer_interval_hours = var.organizer_interval_hours
    app_port                 = var.app_port
    postgres_admin_user      = var.postgres_admin_user
    postgres_admin_password  = var.postgres_admin_password
    postgres_host            = "${local.name}-postgres.postgres.database.azure.com"
    public_base_url          = var.public_base_url != "" ? var.public_base_url : "https://${var.cloudflare_hostname}"
    cloudflare_tunnel_token  = data.cloudflare_zero_trust_tunnel_cloudflared_token.app.token
  }))

  lifecycle {
    # custom_data (cloud-init) only runs on first boot. Azure treats any change
    # as ForceNew and replaces the whole VM (downtime + AllocationFailed risk).
    # App releases and env edits use other paths; recreate the VM deliberately
    # with: terraform apply -replace=azurerm_linux_virtual_machine.main
    ignore_changes = [custom_data]

    precondition {
      condition = var.sms_provider != "twilio" || (
        var.twilio_account_sid != "" && var.twilio_auth_token != "" && var.twilio_from_number != ""
      )
      error_message = "sms_provider=twilio requires twilio_account_sid, twilio_auth_token, and twilio_from_number."
    }

    precondition {
      condition = !var.clerk_enabled || (
        trimspace(var.clerk_publishable_key) != "" &&
        trimspace(var.clerk_frontend_api_url) != "" &&
        (trimspace(var.clerk_secret_key) != "" || trimspace(var.clerk_jwt_key) != "")
      )
      error_message = "clerk_enabled requires clerk_publishable_key, clerk_frontend_api_url, and clerk_secret_key or clerk_jwt_key."
    }
  }

  tags = {
    app = "hangout-automator"
  }
}

resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  managed_disk_id    = azurerm_managed_disk.data.id
  virtual_machine_id = azurerm_linux_virtual_machine.main.id
  lun                = 0
  caching            = "ReadWrite"
}
