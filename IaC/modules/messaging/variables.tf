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

variable "kms_master_key_id" {
  description = "Customer-managed KMS key for SNS encryption, or empty for none. alias/aws/sns blocks End User Messaging Social."
  type        = string
  default     = ""
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
