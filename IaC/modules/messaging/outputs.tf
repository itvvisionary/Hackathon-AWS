# modules/messaging/outputs.tf
# Purpose: Expose the SNS topic ARN and the manual-setup guidance.

output "sns_topic_arn" {
  description = "ARN of the SNS topic for WhatsApp inbound notifications."
  value       = aws_sns_topic.inbound.arn
}

output "sns_topic_name" {
  description = "Name of the SNS topic."
  value       = aws_sns_topic.inbound.name
}

output "kms_key_arn" {
  description = "ARN of the KMS key encrypting the topic (null if unencrypted or an external key is used)."
  value       = var.create_kms_key ? aws_kms_key.sns[0].arn : null
}

output "whatsapp_business_account_id" {
  description = "Pass-through of the linked WABA ID (reference only)."
  value       = var.whatsapp_business_account_id
}

output "event_destination_instructions" {
  description = "Manual step: point the End User Messaging Social event destination at this SNS topic."
  value       = <<-EOT
    MANUAL STEP (not Terraform-provisionable):
    In AWS Console > End User Messaging > Social > your WhatsApp Business Account,
    set the event destination / SNS topic to:
      ${aws_sns_topic.inbound.arn}
    Or via CLI:
      aws socialmessaging update-whatsapp-message-template-* / event destination
      (configure the WABA event destination to this topic ARN).
    WABA ID in use: ${var.whatsapp_business_account_id == "" ? "<not provided>" : var.whatsapp_business_account_id}
  EOT
}
