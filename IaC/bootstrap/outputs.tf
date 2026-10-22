# bootstrap/outputs.tf
# Purpose: Values to copy into GitHub (Settings > Environments > <env> > Variables)
# and into environments/<env>/backend.hcl.

output "state_bucket" {
  description = "Use as 'bucket' in IaC/environments/<env>/backend.hcl."
  value       = data.aws_s3_bucket.state.bucket
}

output "github_environment_variables" {
  description = "Per GitHub Environment: set IAC_ROLE_ARN and APP_ROLE_ARN as environment variables."
  value = {
    for env in local.envs : env => {
      IAC_ROLE_ARN = aws_iam_role.iac[env].arn
      APP_ROLE_ARN = aws_iam_role.app[env].arn
    }
  }
}
