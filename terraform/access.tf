# Cloudflare Access in front of the Tunnel hostname. The FastAPI app still has
# no authentication of its own, so this is the only thing keeping the profile
# list and the invite-sending actions off the open internet.

# allowed_idps is deliberately unset on the applications below, which lets every
# login method configured on the account satisfy this policy. One-time PIN is
# available by default, so an allowed address can sign in with an emailed code
# without registering an external identity provider.
resource "cloudflare_zero_trust_access_policy" "owner" {
  account_id = var.cloudflare_account_id
  name       = "${var.cloudflare_tunnel_name}-owner"
  decision   = "allow"

  include = [
    for email in var.cloudflare_access_allowed_emails : { email = { email = email } }
  ]
}

resource "cloudflare_zero_trust_access_application" "app" {
  account_id       = var.cloudflare_account_id
  name             = var.cloudflare_tunnel_name
  type             = "self_hosted"
  domain           = var.cloudflare_hostname
  session_duration = "24h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.owner.id
    precedence = 1
  }]
}

# Twilio cannot attach CF-Access-Client-Id/CF-Access-Client-Secret headers to a
# webhook, so the inbound SMS route has to skip Access entirely. Cloudflare
# matches the most specific path first, so this application applies to
# /webhooks/sms and the one above still covers everything else.
#
# The route is not left unauthenticated: with SMS_PROVIDER=twilio the handler in
# app/routers/webhooks.py rejects any request whose X-Twilio-Signature does not
# verify against PUBLIC_BASE_URL. Bypassing Access here makes that signature
# check the only layer on this path, which is a deliberate choice.
resource "cloudflare_zero_trust_access_policy" "webhook_bypass" {
  account_id = var.cloudflare_account_id
  name       = "${var.cloudflare_tunnel_name}-twilio-webhook-bypass"
  decision   = "bypass"

  include = [{ everyone = {} }]
}

resource "cloudflare_zero_trust_access_application" "webhook" {
  account_id = var.cloudflare_account_id
  name       = "${var.cloudflare_tunnel_name}-twilio-webhook"
  type       = "self_hosted"
  domain     = "${var.cloudflare_hostname}/webhooks/sms"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.webhook_bypass.id
    precedence = 1
  }]
}
