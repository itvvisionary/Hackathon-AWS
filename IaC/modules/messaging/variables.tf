# modules/messaging/variables.tf
# Purpose: Inputs for the messaging module (SNS topic + WhatsApp linkage refs).

variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Tags applied to resources created by this module."
  type        = map(string)
  default     = {}
}

variable "create_kms_key" {
  description = "Create a customer-managed KMS key (with End User Messaging Social access) and encrypt the topic with it."
  type        = bool
  default     = true
}

variable "kms_master_key_id" {
  description = <<-EOT
    Existing customer-managed KMS key (ARN, ID or alias) used when
    create_kms_key = false. Its key policy must allow social-messaging.amazonaws.com
    kms:GenerateDataKey* and kms:Decrypt. Empty = no encryption.
    alias/aws/sns blocks End User Messaging Social.
  EOT
  type        = string
  default     = ""
}

variable "kms_deletion_window_in_days" {
  description = "Waiting period before the created KMS key is deleted after destroy (7-30)."
  type        = number
  default     = 7

  validation {
    condition     = var.kms_deletion_window_in_days >= 7 && var.kms_deletion_window_in_days <= 30
    error_message = "kms_deletion_window_in_days must be between 7 and 30."
  }
}

variable "whatsapp_business_account_id" {
  description = "Existing linked WhatsApp Business Account ID (manual onboarding output). Reference only."
  type        = string
  default     = ""
}

variable "whatsapp_phone_number_id" {
  description = "Existing WhatsApp phone number ID (manual onboarding output). Reference only."
  type        = string
  default     = ""
}
