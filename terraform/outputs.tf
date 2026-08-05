output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "public_ip" {
  value = azurerm_public_ip.main.ip_address
}

output "app_url" {
  value = "http://${azurerm_public_ip.main.ip_address}"
}

output "ssh_command" {
  value = "ssh ${var.admin_username}@${azurerm_public_ip.main.ip_address}"
}

output "sms_webhook_url" {
  value = "http://${azurerm_public_ip.main.ip_address}/webhooks/sms"
}
