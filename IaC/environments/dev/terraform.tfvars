# environments/dev/terraform.tfvars
# Purpose: Development environment values. Deploy (from IaC/) with:
#   terraform init -backend-config=environments/dev/backend.hcl
#   terraform apply -var-file=environments/dev/terraform.tfvars
# In CI this is done by .github/workflows/iac.yml.

project_name = "wa-ai-rpa"
environment  = "dev"
aws_region   = "us-east-1"

tags = {
  Owner      = "platform-team"
  CostCenter = "cc-dev"
}

# Messaging / WhatsApp (fill in after the manual linking flow).
whatsapp_business_account_id = ""
# End User Messaging Social phone number ID (WHATSAPP_PHONE_NUMBER_ID). This ID
# belongs to the account the number is linked in; after linking the number in
# the new account, replace it with the ID shown there.
whatsapp_phone_number_id = "phone-number-id-7d27e7d5ef9b45bdaa18157170bc8cd3"
sns_kms_master_key_id    = "alias/aws/sns"
meta_api_version         = "v20.0" # META_API_VERSION

# SQS - smaller/looser in dev.
sqs_visibility_timeout_seconds = 1080  # 6x lambda_timeout (AWS recommendation)
sqs_message_retention_seconds  = 86400 # 1 day
sqs_max_receive_count          = 3
sqs_enable_dlq                 = true

# DynamoDB - Terraform creates four on-demand tables named hackathon_<key>_table:
#   hackathon_user_table          PK user_id (S)
#   hackathon_mapping_table       PK conversation_id (S)
#   hackathon_checkpointer_table  PK pk (S), SK sk (S)
#   hackathon_chat_history_table  PK conversation_id (S), SK sk (S)
# No indexes, no replicas, deletion protection off, on-demand capacity.
dynamodb_table_name_prefix   = "hackathon"
dynamodb_billing_mode        = "PAY_PER_REQUEST"
dynamodb_enable_pitr         = false
dynamodb_deletion_protection = false
dynamodb_sse_type            = "AWS_OWNED"

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
  }
}

# Bedrock.
ai_agent_enabled = true
bedrock_mode     = "model"
bedrock_model_id = "us.amazon.nova-2-lite-v1:0" # US cross-region inference profile

# Lambda configuration. The code (App/HackathonWhatsAppCode) and the
# dependency layer (App/layer) are deployed by .github/workflows/app.yml.
lambda_runtime     = "python3.12"
lambda_handler     = "app.lambda_handler"
lambda_timeout     = 180     # 3 minutes
lambda_memory_size = 1024    # LangGraph + FastAPI; 256 MB is too small
lambda_batch_size  = 1       # one WhatsApp message per invocation (no duplicate replies on retry)
lambda_log_level   = "DEBUG" # logging_setup.py: per-function input/output logs

# Function URL for app.py's HTTP routes: test_page.html (/ and /ask) and the
# RPA completion callback (/callback). PUBLIC: anyone with the URL can call it.
lambda_function_url_enabled   = true
lambda_function_url_auth_type = "NONE"
lambda_architecture           = "x86_64" # the App pipeline builds the layer for this

# RPA (external UiPath Orchestrator) - ORDER_API_ENDPOINT.
order_api_endpoint = "https://cloud.uipath.com/itvisieuxena/DefaultTenant/orchestrator_/t/9d21c009-a87c-47f9-b898-bd54b72a3a1b/api_process"
# ORDER_API_KEY is a secret: never put it in this file. Set it in your shell:
#   $env:TF_VAR_order_api_key = "<key>"
