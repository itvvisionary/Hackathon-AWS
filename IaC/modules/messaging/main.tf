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

data "aws_caller_identity" "current" {}

resource "aws_sns_topic" "inbound" {
  name              = "${var.name_prefix}-whatsapp-inbound"
  kms_master_key_id = var.kms_master_key_id != "" ? var.kms_master_key_id : null
  tags              = var.tags
}

# Topic access policy (SNS console default shape). Any principal may use the
# topic, but only when the request comes from this account (AWS:SourceAccount).
# This covers the AWS End User Messaging Social service publishing inbound
# WhatsApp events on behalf of this account.
data "aws_iam_policy_document" "topic_policy" {
  version   = "2008-10-17"
  policy_id = "__default_policy_ID"

  statement {
    sid    = "__default_statement_ID"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    actions = [
      "SNS:Publish",
      "SNS:RemovePermission",
      "SNS:SetTopicAttributes",
      "SNS:DeleteTopic",
      "SNS:ListSubscriptionsByTopic",
      "SNS:GetTopicAttributes",
      "SNS:AddPermission",
      "SNS:Subscribe",
    ]
    resources = [aws_sns_topic.inbound.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "inbound" {
  arn    = aws_sns_topic.inbound.arn
  policy = data.aws_iam_policy_document.topic_policy.json
}
