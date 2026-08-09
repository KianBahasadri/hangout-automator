variable "prefix" {
  description = "Name prefix for Azure resources"
  type        = string
  default     = "hangout"
}

variable "subscription_id" {
  description = "Azure subscription ID. The scripts/deploy/terraform.sh wrapper reads the active az CLI subscription when this is unset."
  type        = string
  default     = ""

  validation {
    condition     = var.subscription_id == "" || can(regex("^[a-f0-9-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID when supplied."
  }
}

# Two separate constraints pin this, and both are subscription-specific. An
# Azure Policy limits deployment to centralus/eastus/canadacentral/eastus2/
# mexicocentral, and of those only mexicocentral will schedule a burstable size
# — everywhere else the whole B-series is restricted, leaving sizes that cost
# roughly six times as much. See docs/deploy.md "Region and VM size capacity"
# before changing either of these.
variable "location" {
  description = "Azure region"
  type        = string
  default     = "mexicocentral"
}

# B2ats_v2 is restricted only in mexicocentral zone 3, and this VM is
# deliberately non-zonal, so regional allocation is unaffected.
variable "vm_size" {
  description = "Cheap burstable VM size"
  type        = string
  default     = "Standard_B2ats_v2"
}

variable "data_disk_size_gb" {
  description = "Persistent managed-disk size for the SQLite database."
  type        = number
  default     = 32

  validation {
    condition     = var.data_disk_size_gb >= 4
    error_message = "data_disk_size_gb must be at least 4 GiB."
  }
}

variable "admin_username" {
  description = "SSH admin username"
  type        = string
  default     = "hangout"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}

variable "git_repo_url" {
  description = "Git repo to clone on the VM (HTTPS or git@)."
  type        = string
  default     = "https://github.com/KianBahasadri/hangout-automator.git"
}

variable "git_branch" {
  description = "Branch to deploy"
  type        = string
  default     = "main"
}

variable "git_revision" {
  description = "Optional commit SHA to deploy instead of the branch tip."
  type        = string
  default     = ""
}

variable "sms_provider" {
  description = "mock or twilio"
  type        = string
  default     = "mock"

  validation {
    condition     = contains(["mock", "twilio"], var.sms_provider)
    error_message = "sms_provider must be 'mock' or 'twilio'."
  }
}

variable "public_base_url" {
  description = "Canonical public URL for the app (e.g. https://hangout.example.com). Required for Twilio webhook signature validation; defaults to the Cloudflare hostname when empty."
  type        = string
  default     = ""
}

variable "clerk_enabled" {
  description = "Require a verified Clerk session for the app UI and JSON API."
  type        = bool
  default     = false
}

variable "clerk_publishable_key" {
  description = "Clerk publishable key exposed to the browser."
  type        = string
  default     = ""
}

variable "clerk_frontend_api_url" {
  description = "Clerk Frontend API URL used to load ClerkJS and the UI package."
  type        = string
  default     = ""
}

variable "clerk_secret_key" {
  description = "Clerk backend secret key used to verify browser sessions."
  type        = string
  default     = ""
  sensitive   = true
}

variable "clerk_jwt_key" {
  description = "Optional Clerk PEM JWT public key for networkless session verification."
  type        = string
  default     = ""
  sensitive   = true
}

variable "clerk_authorized_parties" {
  description = "Comma-separated browser origins accepted by Clerk's authorized-party check."
  type        = string
  default     = ""
}

variable "app_port" {
  description = "Port on which the app listens and the Cloudflare Tunnel connects. The dotenv-aware wrapper maps APP_PORT to this variable."
  type        = number
  default     = 8000

  validation {
    condition     = var.app_port >= 1 && var.app_port <= 65535 && floor(var.app_port) == var.app_port
    error_message = "app_port must be an integer between 1 and 65535."
  }
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID that owns the Tunnel."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hexadecimal Cloudflare account ID."
  }
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID that owns the app's DNS record."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cloudflare_zone_id))
    error_message = "cloudflare_zone_id must be a 32-character hexadecimal Cloudflare zone ID."
  }
}

variable "cloudflare_hostname" {
  description = "Public hostname routed through the Cloudflare Tunnel. Supplied from the ignored .env by scripts/deploy/terraform.sh; no default, so the deployed hostname is not published in this repo."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$", var.cloudflare_hostname))
    error_message = "cloudflare_hostname must be a valid lowercase hostname."
  }
}

variable "cloudflare_tunnel_name" {
  description = "Human-readable name for the remotely managed Cloudflare Tunnel."
  type        = string
  default     = "hangout-automator"
}

variable "cloudflare_access_allowed_emails" {
  description = "Email addresses allowed through Cloudflare Access to the app. Supplied from the ignored .env by scripts/deploy/terraform.sh; no default, so no personal address is published in this repo. An empty list would expose the edge route to nobody allowed by Access."
  type        = list(string)

  validation {
    condition     = length(var.cloudflare_access_allowed_emails) > 0
    error_message = "cloudflare_access_allowed_emails must list at least one address, otherwise nobody can reach the app."
  }

  validation {
    condition     = alltrue([for email in var.cloudflare_access_allowed_emails : can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", email))])
    error_message = "Every cloudflare_access_allowed_emails entry must be an email address."
  }
}

variable "twilio_account_sid" {
  type      = string
  default   = ""
  sensitive = true
}

variable "twilio_auth_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "twilio_from_number" {
  type    = string
  default = ""
}

variable "google_maps_api_key" {
  description = "Optional Google Maps Platform Places API (New) key for location autocomplete."
  type        = string
  default     = ""
  sensitive   = true
}

variable "followup_hours" {
  type    = string
  default = "24,48"
}

variable "organizer_interval_hours" {
  type    = number
  default = 6
}

variable "postgres_admin_user" {
  description = "Administrator login for the Azure Database for PostgreSQL Flexible Server"
  type        = string
  default     = "hangout"
}

variable "postgres_admin_password" {
  description = "Administrator password for the Flexible Server. No default: sourced from POSTGRES_ADMIN_PASSWORD in the ignored .env by scripts/deploy/terraform.sh."
  type        = string
  sensitive   = true
}
