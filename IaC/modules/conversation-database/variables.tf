# modules/conversation-database/variables.tf
# Purpose: Inputs for a single DynamoDB table.

variable "table_name" {
  description = "Exact DynamoDB table name."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]{3,255}$", var.table_name))
    error_message = "table_name must be 3-255 chars: letters, numbers, underscore, hyphen, dot."
  }
}

variable "tags" {
  description = "Tags applied to the table."
  type        = map(string)
  default     = {}
}

variable "billing_mode" {
  description = "DynamoDB billing mode: PAY_PER_REQUEST (on-demand) or PROVISIONED."
  type        = string
  default     = "PAY_PER_REQUEST"

  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.billing_mode)
    error_message = "billing_mode must be PAY_PER_REQUEST or PROVISIONED."
  }
}

variable "hash_key" {
  description = "Partition key attribute name."
  type        = string
}

variable "hash_key_type" {
  description = "Partition key type: S, N, or B."
  type        = string
  default     = "S"

  validation {
    condition     = contains(["S", "N", "B"], var.hash_key_type)
    error_message = "hash_key_type must be S, N, or B."
  }
}

variable "range_key" {
  description = "Sort key attribute name. Empty string means no sort key."
  type        = string
  default     = ""
}

variable "range_key_type" {
  description = "Sort key type: S, N, or B."
  type        = string
  default     = "S"

  validation {
    condition     = contains(["S", "N", "B"], var.range_key_type)
    error_message = "range_key_type must be S, N, or B."
  }
}

variable "read_capacity" {
  description = "Read capacity units (PROVISIONED mode only)."
  type        = number
  default     = 5
}

variable "write_capacity" {
  description = "Write capacity units (PROVISIONED mode only)."
  type        = number
  default     = 5
}

variable "enable_pitr" {
  description = "Enable point-in-time recovery."
  type        = bool
  default     = true
}

variable "deletion_protection_enabled" {
  description = "Prevent the table from being deleted (including by terraform destroy)."
  type        = bool
  default     = false
}

variable "ttl_attribute" {
  description = "TTL attribute name for item expiration. Empty string disables TTL."
  type        = string
  default     = ""
}

variable "sse_type" {
  description = "Encryption-at-rest key type: AWS_OWNED, AWS_MANAGED, or CUSTOMER_MANAGED."
  type        = string
  default     = "AWS_OWNED"

  validation {
    condition     = contains(["AWS_OWNED", "AWS_MANAGED", "CUSTOMER_MANAGED"], var.sse_type)
    error_message = "sse_type must be AWS_OWNED, AWS_MANAGED, or CUSTOMER_MANAGED."
  }
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN. Required when sse_type = CUSTOMER_MANAGED."
  type        = string
  default     = ""
}
