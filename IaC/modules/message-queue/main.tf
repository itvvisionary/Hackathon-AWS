# modules/message-queue/main.tf
# Purpose: Create the main SQS processing queue, an optional dead-letter queue
# with a redrive policy, a queue policy allowing the SNS topic to send messages,
# and the SNS->SQS subscription.

# Dead-letter queue (optional).
resource "aws_sqs_queue" "dlq" {
  count                     = var.enable_dlq ? 1 : 0
  name                      = "${var.name_prefix}-processing-dlq"
  message_retention_seconds = var.dlq_message_retention_seconds
  sqs_managed_sse_enabled   = var.sqs_managed_sse_enabled
  tags                      = var.tags
}

# Main processing queue.
resource "aws_sqs_queue" "main" {
  name                       = "${var.name_prefix}-processing"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  sqs_managed_sse_enabled    = var.sqs_managed_sse_enabled

  redrive_policy = var.enable_dlq ? jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[0].arn
    maxReceiveCount     = var.max_receive_count
  }) : null

  tags = var.tags
}

# Allow the DLQ to only accept redriven messages from the main queue.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  count     = var.enable_dlq ? 1 : 0
  queue_url = aws_sqs_queue.dlq[0].id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.main.arn]
  })
}

# Queue policy: allow the SNS topic to deliver messages to the main queue.
data "aws_iam_policy_document" "queue_policy" {
  statement {
    sid    = "AllowSNSDelivery"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.main.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.sns_topic_arn]
    }
  }
}

resource "aws_sqs_queue_policy" "main" {
  queue_url = aws_sqs_queue.main.id
  policy    = data.aws_iam_policy_document.queue_policy.json
}

# Subscribe the queue to the SNS topic.
resource "aws_sns_topic_subscription" "to_sqs" {
  topic_arn            = var.sns_topic_arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.main.arn
  raw_message_delivery = var.raw_message_delivery

  depends_on = [aws_sqs_queue_policy.main]
}
