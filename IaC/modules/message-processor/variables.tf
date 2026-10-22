# modules/message-processor/variables.tf
# Purpose: Inputs for the Lambda module.

variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Tags applied to resources created by this module."
  type        = map(string)
  default     = {}
}

variable "runtime" {
  description = "Lambda runtime identifier."
  type        = string
  default     = "python3.12"
}

variable "handler" {
  description = "Lambda handler entrypoint as <module>.<function>."
  type        = string
  default     = "app.lambda_handler"

  validation {
    condition     = can(regex("^[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*$", var.handler))
    error_message = "handler must look like module.function, e.g. app.lambda_handler."
  }
}

variable "timeout" {
  description = "Lambda timeout in seconds."
  type        = number
  default     = 30
}

variable "memory_size" {
  description = "Lambda memory size in MB."
  type        = number
  default     = 256
}

variable "function_url_enabled" {
  description = "Create a Lambda Function URL for the application's HTTP routes."
  type        = bool
  default     = false
}

variable "function_url_auth_type" {
  description = "Function URL auth: NONE (public) or AWS_IAM (SigV4-signed callers only)."
  type        = string
  default     = "NONE"

  validation {
    condition     = contains(["NONE", "AWS_IAM"], var.function_url_auth_type)
    error_message = "function_url_auth_type must be NONE or AWS_IAM."
  }
}

variable "batch_size" {
  description = "SQS event source mapping batch size."
  type        = number
  default     = 10
}

variable "maximum_batching_window_in_seconds" {
  description = "Max batching window for the SQS event source mapping."
  type        = number
  default     = 0
}

variable "reserved_concurrency" {
  description = "Reserved concurrent executions. -1 disables reserved concurrency."
  type        = number
  default     = -1
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 14
}

variable "sqs_queue_arn" {
  description = "ARN of the SQS queue that triggers the Lambda."
  type        = string
}

variable "dynamodb_table_arns" {
  description = "ARNs of the DynamoDB tables the Lambda reads/writes."
  type        = list(string)
  default     = []
}

variable "ai_agent_enabled" {
  description = "Whether to attach the Bedrock IAM policy."
  type        = bool
  default     = true
}

variable "bedrock_policy_json" {
  description = "IAM policy JSON for Bedrock access (from the ai-agent module). May be null."
  type        = string
  default     = null
}

variable "environment_variables" {
  description = "Environment variables for the Lambda function. May contain secrets."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "architecture" {
  description = "Lambda instruction set architecture. Must match any attached layers."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "whatsapp_send_enabled" {
  description = "Grant social-messaging:SendWhatsAppMessage so the function can reply on WhatsApp."
  type        = bool
  default     = true
}

variable "whatsapp_phone_number_id" {
  description = <<-EOT
    End User Messaging Social phone number ID (phone-number-id-...) the function
    sends from. Scopes the send permission to that number; empty allows any
    phone number in this account and region.
  EOT
  type        = string
  default     = ""
}

variable "sqs_visibility_timeout_seconds" {
  description = "Visibility timeout of the trigger queue. When set, plan fails if it is shorter than the function timeout (AWS rejects that mapping)."
  type        = number
  default     = null
}
