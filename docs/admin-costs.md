# Admin cost monitoring

Admin Panel → Costs (`GET /admin`, KIAN-535 Phase A). Platform admins only.

## What you see

| Card | Source badge | Data |
|------|--------------|------|
| **Twilio (SMS)** | `estimate` | Counts from `message_logs` for MTD, last 7 days, last 30 days. Optional `$` via env. |
| **Azure** | `link` or `unavailable` | Resource group label + portal deep link when env is set. No live Cost Management query yet. |
| **Cloudflare** | `unavailable` | Note + dashboard billing link. No usage API wired. |

Never blocks the app: every card fails soft. Secrets stay server-side (env only).

## Twilio estimate math

Billable count per window = successful **outbound** + all **inbound** rows in
`message_logs` with `created_at` in range (UTC).

If `TWILIO_SMS_PRICE_ESTIMATE` is set (e.g. `0.0079`):

```text
estimated_usd ≈ billable × TWILIO_SMS_PRICE_ESTIMATE
```

This is **not** Twilio Usage Records, segment-accurate billing, or multi-region
pricing. Failed outbounds are shown separately and are not in the `$` product.
Outbound/inbound success failures and local mock SMS still count in the logs.

## Env vars

| Variable | Purpose |
|----------|---------|
| `TWILIO_SMS_PRICE_ESTIMATE` | Optional float $/message for the estimate column |
| `AZURE_RESOURCE_GROUP` | Label on the Azure card (e.g. `hangout-rg`) |
| `AZURE_SUBSCRIPTION_ID` | With resource group, builds a portal overview deep link |

Twilio send credentials (`TWILIO_ACCOUNT_SID`, etc.) are unrelated; they only
send SMS. Cost estimate does not call Twilio.

### Why Azure can show “unavailable” even when Terraform deployed Azure

Terraform **does** create the subscription resources (resource group
`${prefix}-rg`, VM, Flexible Server, etc.) and outputs `resource_group`. That
is infra only.

The Admin Azure card is driven by **app process env**, not by Terraform state.
`azure_cost_card` returns `source=unavailable` when `AZURE_RESOURCE_GROUP` is
empty. cloud-init’s `/etc/hangout-automator.env` template does **not** write
`AZURE_RESOURCE_GROUP` or `AZURE_SUBSCRIPTION_ID` today, so production shows
unavailable unless those vars are added by hand and units restarted. Setting
them only flips the card to **link** (portal deep link); it still does not pull
live spend.

## Not done yet (backlog)

Ship later when we care; none of this blocks Phase A.

### Wire labels from Terraform (cheap)

- Inject `AZURE_RESOURCE_GROUP` (and subscription id if available) into
  `/etc/hangout-automator.env` from Terraform / cloud-init so a fresh VM gets
  `source=link` without manual env edits.
- Existing VMs still need a one-time env edit + restart (custom_data does not
  update a running host; see [deploy.md](./deploy.md)).
- Optionally surface `TWILIO_SMS_PRICE_ESTIMATE` the same way if we want a
  default estimate on prod.

### Phase B — live vendor amounts

| Vendor | Gap |
|--------|-----|
| **Twilio** | Usage Records / Pricing API instead of (or in addition to) local `message_logs` × flat `$` estimate. Segment and country pricing. |
| **Azure** | Cost Management Query (or Consumption) for the deploy resource group / subscription. Needs identity with Cost Management Reader (or similar) — managed identity on the VM or a service principal; secrets stay server-side. |
| **Cloudflare** | Account billing / usage if we ever leave free-tier tunnel-only; today only a dashboard link. |

Also optional later: cache vendor responses, manual refresh control on `/admin`,
budgets/alerts, fail-soft timeouts so a slow vendor never hangs the page.

## Tests

`tests/test_admin_costs.py` — estimate math and admin-only gate.
