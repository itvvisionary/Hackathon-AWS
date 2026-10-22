# modules/message-processor/main.tf
# Purpose: Create the Lambda function that consumes messages from SQS, plus its
# execution role, scoped IAM policies (logs, SQS, DynamoDB, Bedrock, WhatsApp
# send), log group, SQS event source mapping, and an optional Function URL.
#
# Code ownership: Terraform owns the function's CONFIGURATION (runtime,
# handler, memory, timeout, role, environment variables). The application
# CODE and LAYERS are deployed by the App pipeline (.github/workflows/app.yml),
# so Terraform only uploads a placeholder on first create and then ignores
# code and layer changes (see lifecycle below).

data "aws_partition" "current" {}

locals {
  function_name = "${var.name_prefix}-message-processor"

  # The placeholder module/function must match the configured handler
  # ("app.lambda_handler" -> app.py with lambda_handler).
  handler_module   = split(".", var.handler)[0]
  handler_function = split(".", var.handler)[1]
}

# Placeholder package used only when the function is first created. It fails
# every invocation on purpose, so SQS keeps the messages (and eventually moves
# them to the DLQ) instead of silently dropping them before the real app is
# deployed by the App pipeline.
data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/.build/${local.function_name}-placeholder.zip"

  source {
    filename = "${local.handler_module}.py"
    content  = <<-PY
      def ${local.handler_function}(event, context):
          raise RuntimeError(
              "Placeholder package deployed by Terraform. Run the App workflow "
              "(.github/workflows/app.yml) to deploy the application code."
          )
    PY
  }
}

# ---------------------------------------------------------------------------
# Execution role + trust policy.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

# CloudWatch Logs group (created explicitly so retention is managed).
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# Logging permissions scoped to this function's log group.
data "aws_iam_policy_document" "logging" {
  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "logging" {
  name   = "logging"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.logging.json
}

# SQS consume permissions scoped to the trigger queue.
data "aws_iam_policy_document" "sqs" {
  statement {
    sid    = "ConsumeQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.sqs_queue_arn]
  }
}

resource "aws_iam_role_policy" "sqs" {
  name   = "sqs-consume"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.sqs.json
}

# DynamoDB item access scoped to the given tables (and their indexes).
# DeleteItem is included because LangGraph checkpointers delete thread data.
data "aws_iam_policy_document" "dynamodb" {
  count = length(var.dynamodb_table_arns) > 0 ? 1 : 0

  statement {
    sid    = "TableItemAccess"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:ConditionCheckItem",
    ]
    resources = flatten([
      for arn in var.dynamodb_table_arns : [arn, "${arn}/index/*"]
    ])
  }
}

resource "aws_iam_role_policy" "dynamodb" {
  count  = length(var.dynamodb_table_arns) > 0 ? 1 : 0
  name   = "dynamodb-access"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.dynamodb[0].json
}

# Bedrock access, attached only when the ai-agent module produced a policy.
resource "aws_iam_role_policy" "bedrock" {
  count  = var.ai_agent_enabled && var.bedrock_policy_json != null ? 1 : 0
  name   = "bedrock-invoke"
  role   = aws_iam_role.lambda.id
  policy = var.bedrock_policy_json
}

# WhatsApp replies through AWS End User Messaging Social. The ARN uses the ID
# without its "phone-number-id-" prefix:
#   arn:aws:social-messaging:<region>:<account>:phone-number-id/<id>
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "whatsapp" {
  count = var.whatsapp_send_enabled ? 1 : 0

  statement {
    sid     = "SendWhatsAppMessage"
    effect  = "Allow"
    actions = ["social-messaging:SendWhatsAppMessage"]
    resources = [
      "arn:${data.aws_partition.current.partition}:social-messaging:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:phone-number-id/${var.whatsapp_phone_number_id == "" ? "*" : trimprefix(var.whatsapp_phone_number_id, "phone-number-id-")}",
    ]
  }
}

resource "aws_iam_role_policy" "whatsapp" {
  count  = var.whatsapp_send_enabled ? 1 : 0
  name   = "whatsapp-send"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.whatsapp[0].json
}

# ---------------------------------------------------------------------------
# Lambda function.
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "processor" {
  function_name = local.function_name
  role          = aws_iam_role.lambda.arn
  runtime       = var.runtime
  handler       = var.handler
  timeout       = var.timeout
  memory_size   = var.memory_size
  architectures = [var.architecture]

  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256

  reserved_concurrent_executions = var.reserved_concurrency >= 0 ? var.reserved_concurrency : null

  environment {
    variables = var.environment_variables
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.lambda]

  # Code and layers are owned by the App pipeline. Without this, every IaC
  # apply would roll the function back to the placeholder and drop the layer.
  lifecycle {
    ignore_changes = [
      filename,
      source_code_hash,
      s3_bucket,
      s3_key,
      s3_object_version,
      layers,
    ]
  }
}

# ---------------------------------------------------------------------------
# SQS -> Lambda event source mapping.
# ---------------------------------------------------------------------------
resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn                   = var.sqs_queue_arn
  function_name                      = aws_lambda_function.processor.arn
  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = var.maximum_batching_window_in_seconds
  function_response_types            = ["ReportBatchItemFailures"]

  depends_on = [aws_iam_role_policy.sqs]

  lifecycle {
    precondition {
      condition     = var.sqs_visibility_timeout_seconds == null ? true : var.sqs_visibility_timeout_seconds >= var.timeout
      error_message = "SQS visibility timeout (${coalesce(var.sqs_visibility_timeout_seconds, 0)}s) must be >= the Lambda timeout (${var.timeout}s); AWS recommends 6x."
    }
  }
}

# ---------------------------------------------------------------------------
# Function URL (optional) - HTTPS endpoint for the app's HTTP routes, e.g. the
# RPA completion callback. Auth type NONE makes it public: anyone with the URL
# can call it, so the application must authenticate requests itself.
# ---------------------------------------------------------------------------
resource "aws_lambda_function_url" "this" {
  count              = var.function_url_enabled ? 1 : 0
  function_name      = aws_lambda_function.processor.function_name
  authorization_type = var.function_url_auth_type
}

# Since October 2025 a public (NONE) Function URL needs two resource-policy
# statements: lambda:InvokeFunctionUrl, and lambda:InvokeFunction restricted to
# calls arriving through the Function URL. Without them the URL returns 403.
resource "aws_lambda_permission" "function_url_public_url" {
  count                  = var.function_url_enabled && var.function_url_auth_type == "NONE" ? 1 : 0
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.processor.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "function_url_public_invoke" {
  count                    = var.function_url_enabled && var.function_url_auth_type == "NONE" ? 1 : 0
  statement_id             = "FunctionURLInvokeAllowPublicAccess"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.processor.function_name
  principal                = "*"
  invoked_via_function_url = true
}
