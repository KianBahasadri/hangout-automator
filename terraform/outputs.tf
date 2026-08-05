output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "app_url" {
  value = "https://${var.cloudflare_hostname}"
}

output "sms_webhook_url" {
  value = "https://${var.cloudflare_hostname}/webhooks/sms"
}

output "cloudflare_tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.app.id
}

output "cloudflare_hostname" {
  value = var.cloudflare_hostname
}
