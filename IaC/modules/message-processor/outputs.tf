# modules/message-processor/outputs.tf
# Purpose: Expose the Lambda identifiers and execution role ARN.

output "lambda_arn" {
  description = "ARN of the message-processor Lambda function."
  value       = aws_lambda_function.processor.arn
}

output "lambda_name" {
  description = "Name of the message-processor Lambda function."
  value       = aws_lambda_function.processor.function_name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda.arn
}

output "log_group_name" {
  description = "CloudWatch Logs group name for the Lambda."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "function_url" {
  description = "HTTPS Function URL (null when disabled)."
  value       = var.function_url_enabled ? aws_lambda_function_url.this[0].function_url : null
}
