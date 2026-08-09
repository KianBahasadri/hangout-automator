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
        service  = "http://127.0.0.1:${var.app_port}"
      },
      {
        service = "http_status:404"
      }
    ]
  }
}

# Clerk production instance DNS.
#
# Clerk is registered as a *secondary* application on the app's own hostname
# (see the go-live checklist in docs/deploy.md), so it serves its Frontend API
# and account portal from subdomains of that hostname and sends verification
# mail as it. That needs five CNAMEs. The mail and DKIM targets carry an
# instance-specific label, supplied as clerk_dns_id rather than hardcoded, for
# the same reason cloudflare_hostname has no default: the deployed names stay
# out of this repo. Empty means no records, which is correct while the
# deployment is still on a development instance.
#
# These are deliberately NOT proxied. Cloudflare's proxy terminates TLS with
# its own certificate, which breaks Clerk's Frontend API and blocks the
# certificate it issues for the subdomain; Clerk's setup requires DNS-only.
locals {
  clerk_dns_records = var.clerk_dns_id == "" ? {} : {
    "clerk"           = "frontend-api.clerk.services"
    "accounts"        = "accounts.clerk.services"
    "clkmail"         = "mail.${var.clerk_dns_id}.clerk.services"
    "clk._domainkey"  = "dkim1.${var.clerk_dns_id}.clerk.services"
    "clk2._domainkey" = "dkim2.${var.clerk_dns_id}.clerk.services"
  }
}

#
# ttl is 3600 rather than 1 ("automatic") to match what Clerk's own Cloudflare
# integration writes. The value is irrelevant to Clerk's verification, but the
# dashboard's "configure automatically" button rewrites these records, and a
# config that disagrees turns every re-sync into drift.
resource "cloudflare_dns_record" "clerk" {
  for_each = local.clerk_dns_records

  zone_id = data.cloudflare_zone.app.id
  name    = "${each.key}.${var.cloudflare_hostname}"
  type    = "CNAME"
  content = each.value
  ttl     = 3600
  proxied = false
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
