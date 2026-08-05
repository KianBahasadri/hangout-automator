variable "prefix" {
  description = "Name prefix for Azure resources"
  type        = string
  default     = "hangout"
}

variable "subscription_id" {
  description = "Azure subscription ID. The scripts/terraform.sh wrapper reads the active az CLI subscription when this is unset."
  type        = string
  default     = ""

  validation {
    condition     = var.subscription_id == "" || can(regex("^[a-f0-9-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID when supplied."
  }
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}

variable "vm_size" {
  description = "Cheap burstable VM size"
  type        = string
  default     = "Standard_B1s"
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

variable "cloudflare_account_id" {
  description = "Cloudflare account ID that owns the Tunnel."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hexadecimal Cloudflare account ID."
  }
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for the bahasadri.com DNS record."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.cloudflare_zone_id))
    error_message = "cloudflare_zone_id must be a 32-character hexadecimal Cloudflare zone ID."
  }
}

variable "cloudflare_hostname" {
  description = "Public hostname routed through the Cloudflare Tunnel."
  type        = string
  default     = "hangout.bahasadri.com"

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

variable "followup_hours" {
  type    = string
  default = "24,48"
}

variable "organizer_interval_hours" {
  type    = number
  default = 6
}
