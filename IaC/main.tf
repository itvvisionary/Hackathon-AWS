# main.tf
# Purpose: Root module. Composes the child modules, wiring outputs of one into
# the inputs of the next so Terraform builds the correct dependency graph.
# Application code and the dependency layer live in ../App and are deployed
# by the App pipeline, not by Terraform.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Module: conversation-database (DynamoDB) - one instance per table:
#   user, mapping, checkpointer, chat_history. Independent of Lambda.
# ---------------------------------------------------------------------------
module "conversation_database" {
  source   = "./modules/conversation-database"
  for_each = local.dynamodb_tables

  table_name                  = each.value.name
  tags                        = local.common_tags
  billing_mode                = var.dynamodb_billing_mode
  hash_key                    = each.value.hash_key
  hash_key_type               = each.value.hash_key_type
  range_key                   = each.value.range_key
  range_key_type              = each.value.range_key_type
  ttl_attribute               = each.value.ttl_attribute
  enable_pitr                 = var.dynamodb_enable_pitr
  deletion_protection_enabled = var.dynamodb_deletion_protection
  sse_type                    = var.dynamodb_sse_type
  kms_key_arn                 = var.dynamodb_kms_key_arn
}


# ---------------------------------------------------------------------------
# Module: messaging (SNS topic + End User Messaging Social inputs).
# ---------------------------------------------------------------------------
module "messaging" {
  source = "./modules/messaging"

  name_prefix                  = local.name_prefix
  tags                         = local.common_tags
  kms_master_key_id            = var.sns_kms_master_key_id
  whatsapp_business_account_id = var.whatsapp_business_account_id
  whatsapp_phone_number_id     = var.whatsapp_phone_number_id
}

# ---------------------------------------------------------------------------
# Module: message-queue (SQS + DLQ + SNS subscription).
# ---------------------------------------------------------------------------
module "message_queue" {
  source = "./modules/message-queue"

  name_prefix                = local.name_prefix
  tags                       = local.common_tags
  sns_topic_arn              = module.messaging.sns_topic_arn
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  message_retention_seconds  = var.sqs_message_retention_seconds
  max_receive_count          = var.sqs_max_receive_count
  enable_dlq                 = var.sqs_enable_dlq
}

# ---------------------------------------------------------------------------
# Module: ai-agent (Bedrock IAM policy document, optional).
# ---------------------------------------------------------------------------
module "ai_agent" {
  source = "./modules/ai-agent"

  enabled                 = var.ai_agent_enabled
  name_prefix             = local.name_prefix
  tags                    = local.common_tags
  aws_region              = data.aws_region.current.region
  aws_account_id          = data.aws_caller_identity.current.account_id
  bedrock_mode            = var.bedrock_mode
  bedrock_model_id        = var.bedrock_model_id
  existing_agent_id       = var.bedrock_existing_agent_id
  existing_agent_alias_id = var.bedrock_existing_agent_alias_id
}

# ---------------------------------------------------------------------------
# Artifacts bucket: the App pipeline uploads the dependency layer zip here and
# publishes the layer from S3 (a ~48 MB direct upload can expire its request
# signature on a slow link). Holds only rebuildable artifacts.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.name_prefix}-lambda-artifacts-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
  tags          = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Old layer artifacts are never referenced again once a newer layer version is
# published, so expire them instead of keeping every build forever.
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-old-layer-zips"
    status = "Enabled"
    filter {
      prefix = "layers/"
    }
    expiration {
      days = 90
    }
  }
}


# ---------------------------------------------------------------------------
# Module: message-processor (Lambda + SQS trigger + IAM).
# ---------------------------------------------------------------------------
module "message_processor" {
  source = "./modules/message-processor"

  name_prefix = local.name_prefix
  tags        = local.common_tags

  # Configuration only; code and layers are deployed by the App pipeline.
  runtime     = var.lambda_runtime
  handler     = var.lambda_handler
  timeout     = var.lambda_timeout
  memory_size = var.lambda_memory_size

  # HTTP routes in app.py (test page, /ask, /callback).
  function_url_enabled   = var.lambda_function_url_enabled
  function_url_auth_type = var.lambda_function_url_auth_type
  batch_size             = var.lambda_batch_size
  reserved_concurrency   = var.lambda_reserved_concurrency
  log_retention_days     = var.lambda_log_retention_days
  architecture           = var.lambda_architecture

  # Wiring from the other modules.
  sqs_queue_arn                  = module.message_queue.queue_arn
  sqs_visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  dynamodb_table_arns            = [for t in module.conversation_database : t.table_arn]

  # Bedrock permissions (only attached if ai_agent is enabled).
  ai_agent_enabled    = var.ai_agent_enabled
  bedrock_policy_json = module.ai_agent.iam_policy_json

  # Reply on WhatsApp via End User Messaging Social.
  whatsapp_send_enabled    = true
  whatsapp_phone_number_id = var.whatsapp_phone_number_id

  # Application configuration delivered as environment variables. These are
  # the nine variables the application expects; table names come from the
  # DynamoDB module so they always match the tables Terraform created.
  environment_variables = merge(
    {
      BEDROCK_MODEL_ID         = var.bedrock_model_id
      CHAT_HISTORY_TABLE_NAME  = module.conversation_database["chat_history"].table_name
      CHECKPOINTER_TABLE_NAME  = module.conversation_database["checkpointer"].table_name
      MAPPING_TABLE_NAME       = module.conversation_database["mapping"].table_name
      META_API_VERSION         = var.meta_api_version
      ORDER_API_ENDPOINT       = var.order_api_endpoint
      ORDER_API_KEY            = var.order_api_key
      USERS_TABLE_NAME         = module.conversation_database["user"].table_name
      WHATSAPP_PHONE_NUMBER_ID = var.whatsapp_phone_number_id
      LOG_LEVEL                = var.lambda_log_level
    },
    # Only when invoking an existing Bedrock Agent instead of a model.
    var.bedrock_mode == "agent" ? {
      BEDROCK_AGENT_ID       = var.bedrock_existing_agent_id
      BEDROCK_AGENT_ALIAS_ID = var.bedrock_existing_agent_alias_id
    } : {},
    var.lambda_additional_environment,
  )
}
