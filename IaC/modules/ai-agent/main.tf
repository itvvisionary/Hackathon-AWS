# modules/ai-agent/main.tf
# Purpose: Produce the IAM policy JSON that grants the message-processor Lambda
# permission to invoke the chosen Bedrock capability.
#
# WHAT CAN AND CANNOT BE PROVISIONED (verified against the AWS provider):
#   - Foundation MODELS cannot be "created". Model access is enabled once per
#     account/region in the Bedrock console ("Model access"), then invoked by
#     ID. This module wires IAM for an EXISTING model ID.
#   - Bedrock AGENTS *can* be created with aws_bedrockagent_agent, but they also
#     require a separate "prepare" step and alias management that is awkward in
#     pure Terraform. To stay robust and reusable this module targets an
#     EXISTING agent (agent_id + alias_id) and only wires IAM. Creating agents,
#     knowledge bases and prompts is intentionally out of scope and documented
#     as a separate concern in the README.
#
# The module is optional: when enabled = false it returns an empty policy and
# the Lambda module simply does not attach a Bedrock policy.

locals {
  # Build the list of statements based on mode. Empty when disabled.
  model_statements = var.enabled && var.bedrock_mode == "model" ? [
    {
      Sid    = "InvokeBedrockModel"
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      # Cross-region inference profiles (IDs prefixed us./eu./apac./global.,
      # e.g. us.amazon.nova-2-lite-v1:0) route each request to the foundation
      # model in any region of the profile, so the foundation-model ARN must
      # not be pinned to the deployment region. The profile itself lives in
      # this account and region. bedrock:InvokeModel also covers the Converse API.
      Resource = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:inference-profile/*",
      ]
    }
  ] : []

  agent_statements = var.enabled && var.bedrock_mode == "agent" ? [
    {
      Sid    = "InvokeBedrockAgent"
      Effect = "Allow"
      Action = ["bedrock:InvokeAgent"]
      Resource = [
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent/${var.existing_agent_id == "" ? "*" : var.existing_agent_id}",
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:agent-alias/${var.existing_agent_id == "" ? "*" : var.existing_agent_id}/${var.existing_agent_alias_id == "" ? "*" : var.existing_agent_alias_id}",
      ]
    }
  ] : []

  all_statements = concat(local.model_statements, local.agent_statements)

  # A valid-but-empty policy when disabled, so the consumer can always
  # reference iam_policy_json without conditionals.
  policy_document = length(local.all_statements) > 0 ? {
    Version   = "2012-10-17"
    Statement = local.all_statements
  } : null
}
