# modules/ai-agent/outputs.tf
# Purpose: Expose the Bedrock IAM policy JSON (or null) and the resolved
# identifiers for the Lambda environment and root outputs.

output "iam_policy_json" {
  description = "IAM policy JSON granting Bedrock invoke permissions, or null when disabled."
  value       = local.policy_document != null ? jsonencode(local.policy_document) : null
}

output "active_mode" {
  description = "Resolved integration mode ('model', 'agent', or 'disabled')."
  value       = var.enabled ? var.bedrock_mode : "disabled"
}

output "model_id" {
  description = "Bedrock model ID in use (model mode)."
  value       = var.bedrock_model_id
}

output "agent_id" {
  description = "Bedrock Agent ID in use (agent mode)."
  value       = var.existing_agent_id
}

output "agent_alias_id" {
  description = "Bedrock Agent alias ID in use (agent mode)."
  value       = var.existing_agent_alias_id
}
