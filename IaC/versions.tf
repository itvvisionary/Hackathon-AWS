# versions.tf
# Purpose: Pin the required Terraform core version and provider versions so that
# plans are reproducible across every environment and CI runner.

terraform {
  # 1.10+ for S3 native state locking (use_lockfile in backend.hcl).
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.28+ for aws_lambda_permission.invoked_via_function_url (Function URL
      # access model required since October 2025).
      version = ">= 6.28.0, < 7.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0, < 3.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0, < 4.0.0"
    }
  }
}
