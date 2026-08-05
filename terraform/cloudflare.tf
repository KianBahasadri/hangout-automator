resource "cloudflare_zero_trust_tunnel_cloudflared" "app" {
  account_id = var.cloudflare_account_id
  name       = var.cloudflare_tunnel_name
  config_src = "cloudflare"
}

data "cloudflare_zone" "app" {
  zone_id = var.cloudflare_zone_id
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "app" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.app.id
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "app" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.app.id
  source     = "cloudflare"

  config = {
    ingress = [
      {
        hostname = var.cloudflare_hostname
        service  = "http://127.0.0.1:8000"
      },
      {
        service = "http_status:404"
      }
    ]
  }
}

resource "cloudflare_dns_record" "app" {
  zone_id = data.cloudflare_zone.app.id
  name    = var.cloudflare_hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.app.id}.cfargotunnel.com"
  ttl     = 1
  proxied = true

  lifecycle {
    precondition {
      condition     = data.cloudflare_zone.app.status == "active"
      error_message = "The target Cloudflare zone must be active before creating the Tunnel hostname record."
    }
  }
}
