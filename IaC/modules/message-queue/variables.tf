# modules/message-queue/variables.tf
# Purpose: Inputs for the SQS module. All queue behaviour is configurable.

variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Tags applied to resources created by this module."
  type        = map(string)
  default     = {}
}

variable "sns_topic_arn" {
  description = "ARN of the SNS topic to subscribe the queue to."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "Visibility timeout for the main queue."
  type        = number
  default     = 180
}

variable "message_retention_seconds" {
  description = "Message retention period for the main queue."
  type        = number
  default     = 345600
}

variable "max_receive_count" {
  description = "Receives before a message is redriven to the DLQ."
  type        = number
  default     = 5
}

variable "enable_dlq" {
  description = "Create a dead-letter queue and attach a redrive policy."
  type        = bool
  default     = true
}

variable "dlq_message_retention_seconds" {
  description = "Message retention period for the DLQ."
  type        = number
  default     = 1209600 # 14 days
}

variable "sqs_managed_sse_enabled" {
  description = "Enable SQS-managed server-side encryption (SSE-SQS)."
  type        = bool
  default     = true
}

variable "raw_message_delivery" {
  description = "Deliver the raw SNS message body to SQS (no SNS envelope)."
  type        = bool
  default     = true
}
