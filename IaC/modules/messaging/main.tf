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
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  # Key used to encrypt the topic: the one created below, an existing
  # customer-managed key passed in, or none.
  topic_kms_key = var.create_kms_key ? aws_kms_key.sns[0].arn : (var.kms_master_key_id != "" ? var.kms_master_key_id : null)
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key for the topic. The AWS-managed alias/aws/sns key
# cannot be used: its policy can't be edited, so End User Messaging Social
# could not encrypt the events it publishes and they would be dropped.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "sns_key" {
  count = var.create_kms_key ? 1 : 0

  # Account administrators manage the key through IAM (standard default).
  statement {
    sid       = "EnableAccountAdministration"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  # End User Messaging Social publishes WhatsApp events to the encrypted
  # topic. Statement as documented by AWS, with confused-deputy conditions.
  statement {
    sid       = "AllowEndUserMessagingSocial"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["social-messaging.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:social-messaging:${local.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_kms_key" "sns" {
  count                   = var.create_kms_key ? 1 : 0
  description             = "Encrypts SNS topic ${var.name_prefix}-whatsapp-inbound (WhatsApp inbound events)"
  key_usage               = "ENCRYPT_DECRYPT"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_in_days
  policy                  = data.aws_iam_policy_document.sns_key[0].json
  tags                    = var.tags
}

resource "aws_kms_alias" "sns" {
  count         = var.create_kms_key ? 1 : 0
  name          = "alias/${var.name_prefix}-whatsapp-inbound-sns"
  target_key_id = aws_kms_key.sns[0].key_id
}

resource "aws_sns_topic" "inbound" {
  name              = "${var.name_prefix}-whatsapp-inbound"
  kms_master_key_id = local.topic_kms_key
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
