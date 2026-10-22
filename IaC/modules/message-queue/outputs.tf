# modules/message-queue/outputs.tf
# Purpose: Expose queue identifiers for the Lambda event source and other stacks.

output "queue_arn" {
  description = "ARN of the main processing SQS queue."
  value       = aws_sqs_queue.main.arn
}

output "queue_url" {
  description = "URL of the main processing SQS queue."
  value       = aws_sqs_queue.main.id
}

output "queue_name" {
  description = "Name of the main processing SQS queue."
  value       = aws_sqs_queue.main.name
}

output "dlq_arn" {
  description = "ARN of the dead-letter queue (null if DLQ disabled)."
  value       = var.enable_dlq ? aws_sqs_queue.dlq[0].arn : null
}

output "dlq_url" {
  description = "URL of the dead-letter queue (null if DLQ disabled)."
  value       = var.enable_dlq ? aws_sqs_queue.dlq[0].id : null
}
