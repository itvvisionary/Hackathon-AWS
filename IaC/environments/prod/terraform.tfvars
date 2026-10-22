# environments/prod/terraform.tfvars
# Purpose: Production environment values. Deploy with:
#   terraform workspace select prod || terraform workspace new prod
#   terraform apply -var-file=environments/prod/terraform.tfvars

project_name = "wa-ai-rpa"
environment  = "prod"
aws_region   = "us-east-1"

tags = {
  Owner      = "platform-team"
  CostCenter = "cc-prod"
  Compliance = "pii"
}

# Messaging / WhatsApp (fill in after the manual linking flow).
whatsapp_business_account_id = ""
whatsapp_phone_number_id     = "" # phone-number-id-... of the prod-linked number
sns_kms_master_key_id        = "alias/aws/sns"
meta_api_version             = "v20.0"

# SQS - production retention and generous visibility (>= 6x Lambda timeout).
sqs_visibility_timeout_seconds = 1080   # 6x lambda_timeout
sqs_message_retention_seconds  = 345600 # 4 days
sqs_max_receive_count          = 5
sqs_enable_dlq                 = true

# DynamoDB - the same four tables, created by Terraform, named
# hackathon_prod_<key>_table. PITR and deletion protection on for prod.
dynamodb_table_name_prefix   = "hackathon_prod"
dynamodb_billing_mode        = "PAY_PER_REQUEST"
dynamodb_enable_pitr         = true
dynamodb_deletion_protection = true
dynamodb_sse_type            = "AWS_MANAGED"

dynamodb_tables = {
  user = {
    hash_key = "user_id"
  }
  mapping = {
    hash_key = "conversation_id"
  }
  checkpointer = {
    hash_key      = "pk"
    range_key     = "sk"
    ttl_attribute = "expires_at" # checkpointer.py writes expires_at = now + 7 days
  }
  chat_history = {
    hash_key  = "conversation_id"
    range_key = "sk"
    # ttl_attribute = "expires_at"  # uncomment to expire old chat history
  }
}

# Bedrock.
ai_agent_enabled = true
bedrock_mode     = "model"
bedrock_model_id = "us.amazon.nova-2-lite-v1:0"

# Lambda - hardened sizing and reserved concurrency to protect downstream RPA.
lambda_runtime              = "python3.12"
lambda_handler              = "app.lambda_handler"
lambda_timeout              = 180 # 3 minutes
lambda_memory_size          = 1024
lambda_batch_size           = 1
lambda_log_level            = "INFO" # DEBUG would log customer PII
lambda_reserved_concurrency = 20
lambda_log_retention_days   = 30

# Function URL (/ask, /callback). Disabled in prod: the app does no request
# authentication, so a public URL lets anyone chat with the agent or post fake
# order-completion messages. Enable only after adding a callback secret check.
lambda_function_url_enabled   = false
lambda_function_url_auth_type = "NONE"
lambda_architecture           = "x86_64" # the App pipeline builds the layer for this

# RPA (external UiPath Orchestrator) - ORDER_API_ENDPOINT for the prod tenant/folder.
order_api_endpoint = "" # e.g. "https://cloud.uipath.com/<org>/<tenant>/orchestrator_/t/<id>/api_process"
# ORDER_API_KEY: set TF_VAR_order_api_key from your CI secret store, never here.
