variable "subscription_id" {
  description = "Azure subscription that owns the Terraform state resources."
  type        = string

  validation {
    condition     = can(regex("^[a-f0-9-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID."
  }
}

variable "location" {
  description = "Azure region for the state resource group."
  type        = string
  default     = "eastus"
}

variable "prefix" {
  description = "Prefix used for the state resource group and storage account."
  type        = string
  default     = "hangout"
}

variable "operator_object_id" {
  description = "Optional Azure AD object ID to grant blob state access; defaults to the current az login principal."
  type        = string
  default     = ""
}
