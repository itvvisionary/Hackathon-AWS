# variables.tf
# Purpose: All root-level input variables. These drive naming, tagging, region,
# and are passed down into child modules. No environment-specific value is
# hardcoded anywhere in the codebase; everything flows through these variables.

# ---------------------------------------------------------------------------
# Global / naming
# ---------------------------------------------------------------------------

variable "project_name" {
  description = "Project name used as a prefix for all resource names."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.project_name))
    error_message = "project_name must be 3-32 chars, lowercase alphanumeric and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment name (e.g. dev, staging, prod)."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod", "test", "sandbox"], var.environment)
    error_message = "environment must be one of: dev, staging, prod, test, sandbox."
  }
}

variable "aws_region" {
  description = "AWS region to deploy all resources into."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a valid region string, e.g. us-east-1."
  }
}

variable "tags" {
  description = "Additional tags merged into the common tag set applied to all resources."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Messaging (SNS + End User Messaging Social / WhatsApp)
# ---------------------------------------------------------------------------

variable "whatsapp_business_account_id" {
  description = <<-EOT
    Existing WhatsApp Business Account (WABA) ID linked in AWS End User Messaging Social.
    This CANNOT be created by Terraform; it is produced by the manual Meta embedded
    sign-up / linking flow in the AWS Console. Leave empty to only provision the SNS
    topic and wire the event destination manually later.
  EOT
  type        = string
  default     = ""
}

variable "whatsapp_phone_number_id" {
  description = <<-EOT
    End User Messaging Social phone number ID (phone-number-id-...) of the linked
    WhatsApp number. Injected as WHATSAPP_PHONE_NUMBER_ID and used to scope the
    Lambda's SendWhatsAppMessage permission.
  EOT
  type        = string
  default     = ""
}

variable "sns_create_kms_key" {
  description = <<-EOT
    Create a customer-managed KMS key for the WhatsApp inbound SNS topic, with a
    key policy that lets End User Messaging Social encrypt the events it
    publishes. When false, sns_kms_master_key_id is used instead.
  EOT
  type        = bool
  default     = true
}

variable "sns_kms_master_key_id" {
  description = <<-EOT
    Existing customer-managed KMS key for the topic, used only when
    sns_create_kms_key = false. Empty string = no encryption at rest.
    Do NOT use the AWS-managed key "alias/aws/sns": its key policy cannot be
    edited and does not let the End User Messaging Social service use it, so
    WhatsApp events are silently dropped. The key's policy must allow
    social-messaging.amazonaws.com kms:GenerateDataKey* and kms:Decrypt.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.sns_kms_master_key_id != "alias/aws/sns"
    error_message = "alias/aws/sns cannot be used: End User Messaging Social cannot publish to topics encrypted with it. Use \"\" or a customer-managed KMS key."
  }
}

# ---------------------------------------------------------------------------
# Message queue (SQS)
# ---------------------------------------------------------------------------

variable "sqs_visibility_timeout_seconds" {
  description = "SQS visibility timeout. Should be >= Lambda timeout (recommend 6x)."
  type        = number
  default     = 180

  validation {
    condition     = var.sqs_visibility_timeout_seconds >= 0 && var.sqs_visibility_timeout_seconds <= 43200
    error_message = "sqs_visibility_timeout_seconds must be between 0 and 43200."
  }
}

variable "sqs_message_retention_seconds" {
  description = "How long SQS retains a message (seconds)."
  type        = number
  default     = 345600 # 4 days
}

variable "sqs_max_receive_count" {
  description = "Number of receives before a message is moved to the dead-letter queue."
  type        = number
  default     = 5
}

variable "sqs_enable_dlq" {
  description = "Whether to create a dead-letter queue and redrive policy."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Conversation database (DynamoDB)
# ---------------------------------------------------------------------------

variable "dynamodb_billing_mode" {
  description = "DynamoDB billing mode: PAY_PER_REQUEST or PROVISIONED."
  type        = string
  default     = "PAY_PER_REQUEST"

  validation {
    condition     = contains(["PAY_PER_REQUEST", "PROVISIONED"], var.dynamodb_billing_mode)
    error_message = "dynamodb_billing_mode must be PAY_PER_REQUEST or PROVISIONED."
  }
}

variable "dynamodb_table_name_prefix" {
  description = <<-EOT
    Prefix for table names, which follow "<prefix>_<key>_table"
    (e.g. "hackathon" -> hackathon_user_table). Empty string uses
    "<project>_<environment>" with hyphens replaced by underscores.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.dynamodb_table_name_prefix == "" || can(regex("^[a-zA-Z0-9_.-]+$", var.dynamodb_table_name_prefix))
    error_message = "dynamodb_table_name_prefix may contain only letters, numbers, underscore, hyphen, dot."
  }
}

variable "dynamodb_tables" {
  description = <<-EOT
    DynamoDB tables to manage, keyed by logical name. The key is used in the
    table name; user, mapping, checkpointer and chat_history are required
    because the Lambda environment references them.
    Set "name" to override the generated table name. Unset optional fields
    fall back to the dynamodb_* defaults below.
  EOT
  type = map(object({
    name           = optional(string)
    hash_key       = string
    hash_key_type  = optional(string, "S")
    range_key      = optional(string, "")
    range_key_type = optional(string, "S")
    ttl_attribute  = optional(string, "")
  }))

  default = {
    user = {
      hash_key = "user_id"
    }
    mapping = {
      hash_key = "conversation_id"
    }
    checkpointer = {
      hash_key  = "pk"
      range_key = "sk"
    }
    chat_history = {
      hash_key  = "conversation_id"
      range_key = "sk"
    }
  }

  validation {
    condition     = alltrue([for k in keys(var.dynamodb_tables) : can(regex("^[a-z][a-z0-9_]*$", k))])
    error_message = "dynamodb_tables keys must be lowercase snake_case (e.g. chat_history)."
  }

  # The Lambda environment references these four tables by key.
  validation {
    condition     = alltrue([for k in ["user", "mapping", "checkpointer", "chat_history"] : contains(keys(var.dynamodb_tables), k)])
    error_message = "dynamodb_tables must include the keys: user, mapping, checkpointer, chat_history."
  }
}

variable "dynamodb_enable_pitr" {
  description = "Enable point-in-time recovery on all tables."
  type        = bool
  default     = true
}

variable "dynamodb_deletion_protection" {
  description = "Enable deletion protection on all tables. Recommended for prod."
  type        = bool
  default     = false
}

variable "dynamodb_sse_type" {
  description = "Encryption-at-rest key type for all tables: AWS_OWNED, AWS_MANAGED, or CUSTOMER_MANAGED."
  type        = string
  default     = "AWS_OWNED"

  validation {
    condition     = contains(["AWS_OWNED", "AWS_MANAGED", "CUSTOMER_MANAGED"], var.dynamodb_sse_type)
    error_message = "dynamodb_sse_type must be AWS_OWNED, AWS_MANAGED, or CUSTOMER_MANAGED."
  }
}

variable "dynamodb_kms_key_arn" {
  description = "Customer-managed KMS key ARN when dynamodb_sse_type = CUSTOMER_MANAGED."
  type        = string
  default     = ""
}


# ---------------------------------------------------------------------------
# AI Agent (Amazon Bedrock)
# ---------------------------------------------------------------------------

variable "ai_agent_enabled" {
  description = "Master switch for the Bedrock AI-agent integration module."
  type        = bool
  default     = true
}

variable "bedrock_mode" {
  description = "Which Bedrock integration to wire IAM for: 'model' (InvokeModel) or 'agent' (InvokeAgent)."
  type        = string
  default     = "model"

  validation {
    condition     = contains(["model", "agent"], var.bedrock_mode)
    error_message = "bedrock_mode must be 'model' or 'agent'."
  }
}

variable "bedrock_model_id" {
  description = "Existing Bedrock foundation/inference-profile model ID to invoke (e.g. anthropic.claude-3-5-sonnet-20240620-v1:0)."
  type        = string
  default     = ""
}

variable "bedrock_existing_agent_id" {
  description = "Existing Bedrock Agent ID to invoke. Used when bedrock_mode = 'agent'."
  type        = string
  default     = ""
}

variable "bedrock_existing_agent_alias_id" {
  description = "Existing Bedrock Agent alias ID. Used when bedrock_mode = 'agent'."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Message processor (Lambda)
# ---------------------------------------------------------------------------

variable "lambda_runtime" {
  description = "Lambda runtime identifier."
  type        = string
  default     = "python3.12"
}

variable "lambda_handler" {
  description = "Lambda handler entrypoint (app.lambda_handler routes SQS and Function URL events)."
  type        = string
  default     = "app.lambda_handler"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds (max 900). sqs_visibility_timeout_seconds must be >= this value."
  type        = number
  default     = 30

  validation {
    condition     = var.lambda_timeout >= 1 && var.lambda_timeout <= 900
    error_message = "lambda_timeout must be between 1 and 900 seconds."
  }
}

variable "lambda_memory_size" {
  description = "Lambda memory size in MB. LangGraph + FastAPI need at least 512 MB."
  type        = number
  default     = 1024

  validation {
    condition     = var.lambda_memory_size >= 128 && var.lambda_memory_size <= 10240
    error_message = "lambda_memory_size must be between 128 and 10240 MB."
  }
}

variable "lambda_batch_size" {
  description = <<-EOT
    SQS event source mapping batch size. Keep at 1: if one record in a larger
    batch makes the invocation time out, SQS redelivers the whole batch, so
    already-processed messages are re-run (duplicate WhatsApp replies and orders).
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.lambda_batch_size >= 1 && var.lambda_batch_size <= 10000
    error_message = "lambda_batch_size must be between 1 and 10000."
  }
}

variable "lambda_log_level" {
  description = <<-EOT
    Application log level, injected as LOG_LEVEL (read by logging_setup.py).
    DEBUG logs every function's inputs and outputs, including customer names,
    emails, addresses and message text, so use it only outside production.
  EOT
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.lambda_log_level)
    error_message = "lambda_log_level must be DEBUG, INFO, WARNING or ERROR."
  }
}

variable "lambda_function_url_enabled" {
  description = "Create a Function URL for the app's HTTP routes: GET / (test page), POST /ask, POST /callback (RPA completion)."
  type        = bool
  default     = false
}

variable "lambda_function_url_auth_type" {
  description = <<-EOT
    NONE = public HTTPS endpoint (needed for the browser test page and an
    unsigned RPA callback; the app itself does no authentication).
    AWS_IAM = callers must sign requests with SigV4.
  EOT
  type        = string
  default     = "NONE"

  validation {
    condition     = contains(["NONE", "AWS_IAM"], var.lambda_function_url_auth_type)
    error_message = "lambda_function_url_auth_type must be NONE or AWS_IAM."
  }
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrent executions for the Lambda. -1 disables reserved concurrency."
  type        = number
  default     = -1
}

variable "lambda_log_retention_days" {
  description = "CloudWatch Logs retention in days for the Lambda log group."
  type        = number
  default     = 14
}

variable "lambda_additional_environment" {
  description = "Extra environment variables merged into the Lambda configuration."
  type        = map(string)
  default     = {}
}

variable "lambda_architecture" {
  description = "Lambda architecture (x86_64 or arm64). The App pipeline reads it from the function and builds the layer to match."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.lambda_architecture)
    error_message = "lambda_architecture must be x86_64 or arm64."
  }
}

# ---------------------------------------------------------------------------
# RPA integration (external system)
# ---------------------------------------------------------------------------

variable "order_api_endpoint" {
  description = <<-EOT
    External RPA (UiPath Orchestrator) endpoint the Lambda calls to start the
    order process. Injected as ORDER_API_ENDPOINT. No AWS resource is created for it.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.order_api_endpoint == "" || startswith(var.order_api_endpoint, "https://")
    error_message = "order_api_endpoint must be an https:// URL."
  }
}

variable "order_api_key" {
  description = <<-EOT
    API key / token for the RPA order endpoint. Injected as ORDER_API_KEY.
    Do NOT put it in a committed tfvars file; set TF_VAR_order_api_key in your
    shell or CI secret store. It is stored in Terraform state and in the Lambda
    configuration (encrypted at rest with the Lambda service key).
  EOT
  type        = string
  default     = ""
  sensitive   = true
}

# ---------------------------------------------------------------------------
# WhatsApp (Meta) API settings used by the Lambda to reply
# ---------------------------------------------------------------------------

variable "meta_api_version" {
  description = "Meta Graph API version passed to SendWhatsAppMessage. Injected as META_API_VERSION."
  type        = string
  default     = "v20.0"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+$", var.meta_api_version))
    error_message = "meta_api_version must look like v20.0."
  }
}
