# providers.tf
# Purpose: Configure the AWS provider. Region and default tags are driven by
# variables so the same code deploys to any region/environment without edits.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
