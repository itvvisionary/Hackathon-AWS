# modules/ai-agent/variables.tf
# Purpose: Inputs for the Bedrock integration module.

variable "enabled" {
  description = "Master switch. When false the module emits an empty IAM policy and no Bedrock wiring."
  type        = bool
  default     = true
}

variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Tags (reserved for future provisioned resources)."
  type        = map(string)
  default     = {}
}

variable "aws_region" {
  description = "AWS region, used to scope Bedrock ARNs."
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID, used to scope Bedrock agent ARNs."
  type        = string
}

variable "bedrock_mode" {
  description = "Integration mode: 'model' (InvokeModel) or 'agent' (InvokeAgent)."
  type        = string
  default     = "model"

  validation {
    condition     = contains(["model", "agent"], var.bedrock_mode)
    error_message = "bedrock_mode must be 'model' or 'agent'."
  }
}

variable "bedrock_model_id" {
  description = "Existing Bedrock model / inference-profile ID to invoke (model mode)."
  type        = string
  default     = ""
}

variable "existing_agent_id" {
  description = "Existing Bedrock Agent ID to invoke (agent mode)."
  type        = string
  default     = ""
}

variable "existing_agent_alias_id" {
  description = "Existing Bedrock Agent alias ID to invoke (agent mode)."
  type        = string
  default     = ""
}
