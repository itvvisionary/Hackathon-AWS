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
  description = "KMS key id/alias/ARN for SNS encryption. Use 'alias/aws/sns' for the AWS-managed key."
  type        = string
  default     = "alias/aws/sns"
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
