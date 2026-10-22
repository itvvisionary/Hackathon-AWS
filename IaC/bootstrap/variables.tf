# bootstrap/variables.tf
# Purpose: Inputs for the one-time CI bootstrap. project_name and the
# environment names must match the main stack (IaC/environments/*), because
# the App role's permissions are scoped to the resource names derived from them.

variable "aws_region" {
  description = "Region of the main stack (Lambda, layer, artifacts bucket) and the state bucket."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Same project_name as the main stack's tfvars."
  type        = string
  default     = "wa-ai-rpa"
}

variable "environments" {
  description = "Environment names. Each gets an IaC role and an App role, and must exist as a GitHub Environment."
  type        = list(string)
  default     = ["dev", "prod"]
}

variable "github_repository" {
  description = "GitHub repository allowed to assume the roles, as owner/name (e.g. my-org/whatsapp-ai-rpa)."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC identity provider. Set false if the account already has one (only one is allowed per account)."
  type        = bool
  default     = true
}

variable "state_bucket_name" {
  description = "Existing S3 bucket holding the main stack's Terraform state. Must match 'bucket' in IaC/environments/*/backend.hcl."
  type        = string
  default     = "state-file-hackathon-aws"
}
