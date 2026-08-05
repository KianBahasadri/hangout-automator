variable "prefix" {
  description = "Name prefix for Azure resources"
  type        = string
  default     = "hangout"
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
  description = "Git repo to clone on the VM (HTTPS or git@). Leave empty to skip clone; app dir must be provisioned another way."
  type        = string
  default     = ""
}

variable "git_branch" {
  description = "Branch to deploy"
  type        = string
  default     = "main"
}

variable "sms_provider" {
  description = "mock or twilio"
  type        = string
  default     = "mock"
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

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to SSH (lock this down in production)"
  type        = string
  default     = "0.0.0.0/0"
}


