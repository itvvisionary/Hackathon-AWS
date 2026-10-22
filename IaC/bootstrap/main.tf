# bootstrap/main.tf
# Purpose: One-time setup that the CI pipelines need before they can run:
#   1. (The Terraform state bucket already exists: var.state_bucket_name.
#      It is referenced here, not created or managed.)
#   2. GitHub Actions OIDC identity provider (no long-lived AWS keys in GitHub).
#   3. Per environment, two roles assumable only from that GitHub Environment:
#      - <project>-<env>-github-iac : runs Terraform (.github/workflows/iac.yml)
#      - <project>-<env>-github-app : deploys code + layer (.github/workflows/app.yml),
#        scoped to that environment's function, layer and artifacts bucket.
# Run locally once with local state: terraform init && terraform apply.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = var.aws_region
  oidc_url   = "token.actions.githubusercontent.com"
  oidc_arn   = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  envs       = toset(var.environments)
}

# ---------------------------------------------------------------------------
# Existing Terraform state bucket (read only; fails early if it is missing).
# ---------------------------------------------------------------------------
data "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
}

# ---------------------------------------------------------------------------
# GitHub OIDC provider
# ---------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_url}"
}

# Trust: only jobs in this repository that run in the matching GitHub
# Environment ("environment: dev" in the workflow) can assume the role.
data "aws_iam_policy_document" "trust" {
  for_each = local.envs

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = ["repo:${var.github_repository}:environment:${each.key}"]
    }
  }
}

# ---------------------------------------------------------------------------
# IaC role: runs terraform plan/apply for the whole main stack, which creates
# IAM roles, Lambda, SQS, SNS, DynamoDB and S3. That needs broad rights, so it
# uses AdministratorAccess; protect it with a required reviewer on the GitHub
# Environment (Settings > Environments > prod > Required reviewers).
# ---------------------------------------------------------------------------
resource "aws_iam_role" "iac" {
  for_each             = local.envs
  name                 = "${var.project_name}-${each.key}-github-iac"
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "iac_admin" {
  for_each   = local.envs
  role       = aws_iam_role.iac[each.key].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AdministratorAccess"
}

# ---------------------------------------------------------------------------
# App role: least privilege for deploying code and layers to one environment.
# Names follow the main stack: <project>-<env>-message-processor,
# <project>-<env>-langgraph, <project>-<env>-lambda-artifacts-<account>.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "app" {
  for_each = local.envs

  statement {
    sid = "DeployFunction"
    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${var.project_name}-${each.key}-message-processor"]
  }

  statement {
    sid       = "PublishLayer"
    actions   = ["lambda:PublishLayerVersion"]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:layer:${var.project_name}-${each.key}-langgraph"]
  }

  # Attaching a layer requires reading the layer version.
  statement {
    sid       = "ReadLayerVersions"
    actions   = ["lambda:GetLayerVersion"]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:layer:*:*"]
  }

  # ListLayerVersions does not support resource-level permissions.
  statement {
    sid       = "ListLayerVersions"
    actions   = ["lambda:ListLayerVersions"]
    resources = ["*"]
  }

  # PublishLayerVersion from S3 reads the object with the caller's permissions.
  statement {
    sid       = "LayerArtifacts"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:${local.partition}:s3:::${var.project_name}-${each.key}-lambda-artifacts-${local.account_id}/layers/*"]
  }
}

resource "aws_iam_role" "app" {
  for_each             = local.envs
  name                 = "${var.project_name}-${each.key}-github-app"
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "app" {
  for_each = local.envs
  name     = "deploy-lambda-code-and-layer"
  role     = aws_iam_role.app[each.key].id
  policy   = data.aws_iam_policy_document.app[each.key].json
}
