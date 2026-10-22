# backend.tf
# Purpose: Remote state in the existing S3 bucket "state-file-hackathon-aws",
# shared by the GitHub Actions IaC workflow and local runs.
#
# Only the state file path (key) differs per environment, so dev and prod keep
# separate state. It comes from environments/<env>/backend.hcl:
#   terraform init -backend-config=environments/dev/backend.hcl
# Backend blocks cannot use variables, which is why the key is passed this way.

terraform {
  backend "s3" {
    bucket       = "state-file-hackathon-aws"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true # S3 native locking (Terraform >= 1.10), no DynamoDB table
  }
}
