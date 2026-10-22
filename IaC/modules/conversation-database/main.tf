# modules/conversation-database/main.tf
# Purpose: Create ONE DynamoDB table with configurable keys, billing mode,
# encryption at rest, point-in-time recovery, TTL and deletion protection.
# The root module calls this module once per table with for_each, so the same
# code backs the user, mapping, checkpointer and chat-history tables.
# Independent of the Lambda module.

resource "aws_dynamodb_table" "this" {
  name         = var.table_name
  billing_mode = var.billing_mode
  hash_key     = var.hash_key
  range_key    = var.range_key != "" ? var.range_key : null

  # Provisioned capacity only applies when billing_mode = PROVISIONED.
  read_capacity  = var.billing_mode == "PROVISIONED" ? var.read_capacity : null
  write_capacity = var.billing_mode == "PROVISIONED" ? var.write_capacity : null

  deletion_protection_enabled = var.deletion_protection_enabled

  # Only key attributes are declared; all other attributes are schemaless.
  attribute {
    name = var.hash_key
    type = var.hash_key_type
  }

  dynamic "attribute" {
    for_each = var.range_key != "" ? [1] : []
    content {
      name = var.range_key
      type = var.range_key_type
    }
  }

  # Encryption at rest is always on in DynamoDB. The type selects the key:
  #   AWS_OWNED        - AWS-owned key (DynamoDB default, no block emitted)
  #   AWS_MANAGED      - AWS-managed KMS key alias/aws/dynamodb
  #   CUSTOMER_MANAGED - your CMK (kms_key_arn required)
  dynamic "server_side_encryption" {
    for_each = var.sse_type == "AWS_OWNED" ? [] : [1]
    content {
      enabled     = true
      kms_key_arn = var.sse_type == "CUSTOMER_MANAGED" ? var.kms_key_arn : null
    }
  }

  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  dynamic "ttl" {
    for_each = var.ttl_attribute != "" ? [1] : []
    content {
      enabled        = true
      attribute_name = var.ttl_attribute
    }
  }

  tags = var.tags

  lifecycle {
    precondition {
      condition     = var.sse_type != "CUSTOMER_MANAGED" || var.kms_key_arn != ""
      error_message = "kms_key_arn is required when sse_type is CUSTOMER_MANAGED."
    }
  }
}
