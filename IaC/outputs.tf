# outputs.tf
# Purpose: Root outputs. Exposes the key resource identifiers so this stack can
# be consumed by other Terraform projects (e.g. via remote state) or inspected
# after apply.

output "sns_topic_arn" {
  description = "ARN of the SNS topic that receives WhatsApp inbound notifications."
  value       = module.messaging.sns_topic_arn
}

output "sqs_queue_arn" {
  description = "ARN of the primary SQS processing queue."
  value       = module.message_queue.queue_arn
}

output "sqs_queue_url" {
  description = "URL of the primary SQS processing queue."
  value       = module.message_queue.queue_url
}

output "sqs_dlq_arn" {
  description = "ARN of the dead-letter queue (null if disabled)."
  value       = module.message_queue.dlq_arn
}

output "dynamodb_table_names" {
  description = "DynamoDB table names keyed by logical name (user, mapping, checkpointer, chat_history)."
  value       = { for k, t in module.conversation_database : k => t.table_name }
}

output "dynamodb_table_arns" {
  description = "DynamoDB table ARNs keyed by logical name (user, mapping, checkpointer, chat_history)."
  value       = { for k, t in module.conversation_database : k => t.table_arn }
}

output "lambda_function_arn" {
  description = "ARN of the message-processor Lambda function."
  value       = module.message_processor.lambda_arn
}

output "lambda_function_name" {
  description = "Name of the message-processor Lambda function."
  value       = module.message_processor.lambda_name
}

output "lambda_function_url" {
  description = "Function URL base for the test page and the RPA callback (POST <url>/callback). Null if disabled."
  value       = module.message_processor.function_url
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role (useful for cross-stack trust policies)."
  value       = module.message_processor.lambda_role_arn
}

output "bedrock_mode" {
  description = "Active Bedrock integration mode (model or agent)."
  value       = module.ai_agent.active_mode
}

output "bedrock_model_id" {
  description = "Bedrock model ID the Lambda is configured to invoke."
  value       = module.ai_agent.model_id
}

output "bedrock_agent_id" {
  description = "Bedrock Agent ID the Lambda is configured to invoke (agent mode)."
  value       = module.ai_agent.agent_id
}

output "whatsapp_event_destination_setup" {
  description = "Human-readable reminder of the manual step to point End User Messaging Social at the SNS topic."
  value       = module.messaging.event_destination_instructions
}

output "artifacts_bucket_name" {
  description = "Private S3 bucket the App pipeline uploads the layer zip to."
  value       = aws_s3_bucket.artifacts.bucket
}

output "app_deploy_config" {
  description = "Values to set as GitHub environment variables for the App workflow (.github/workflows/app.yml)."
  value = {
    AWS_REGION           = data.aws_region.current.region
    LAMBDA_FUNCTION_NAME = module.message_processor.lambda_name
    LAYER_NAME           = "${local.name_prefix}-langgraph"
    ARTIFACTS_BUCKET     = aws_s3_bucket.artifacts.bucket
  }
}
