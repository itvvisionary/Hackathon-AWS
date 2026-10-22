# modules/messaging/main.tf
# Purpose: Provision the SNS topic that AWS End User Messaging Social publishes
# inbound WhatsApp events to, plus a topic policy that allows the
# social-messaging service to publish.
#
# IMPORTANT - what Terraform CANNOT do here:
#   The linking of a WhatsApp Business Account (WABA) to AWS End User Messaging
#   Social happens through a Meta "embedded sign-up" OAuth flow in the AWS
#   Console (or the LinkWhatsAppBusinessAccount API, which still requires an
#   interactive Meta auth token). There is no aws_* resource in the Terraform
#   AWS provider to create the WABA link or its "event destination". Terraform
#   therefore provisions the SNS topic and the IDs are provided as inputs; the
#   event destination is pointed at this topic as a documented manual step.

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_sns_topic" "inbound" {
  name              = "${var.name_prefix}-whatsapp-inbound"
  kms_master_key_id = var.kms_master_key_id != "" ? var.kms_master_key_id : null
  tags              = var.tags
}

# Allow the AWS End User Messaging Social service principal to publish to the
# topic, scoped to this account.
data "aws_iam_policy_document" "topic_policy" {
  statement {
    sid    = "AllowSocialMessagingPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["social-messaging.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.inbound.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  # Allow account administrators standard management of the topic.
  statement {
    sid    = "AllowAccountOwnerManage"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions = [
      "SNS:Subscribe",
      "SNS:GetTopicAttributes",
      "SNS:SetTopicAttributes",
      "SNS:ListSubscriptionsByTopic",
    ]
    resources = [aws_sns_topic.inbound.arn]
  }
}

resource "aws_sns_topic_policy" "inbound" {
  arn    = aws_sns_topic.inbound.arn
  policy = data.aws_iam_policy_document.topic_policy.json
}
