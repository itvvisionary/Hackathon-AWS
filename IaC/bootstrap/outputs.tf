# bootstrap/outputs.tf
# Purpose: Values to copy into GitHub (Settings > Environments > <env> > Variables)
# and into environments/<env>/backend.hcl.

output "state_bucket" {
  description = "Use as 'bucket' in IaC/environments/<env>/backend.hcl."
  value       = data.aws_s3_bucket.state.bucket
}

output "github_environment_variables" {
  description = <<-EOT
    Per GitHub Environment: AWS_ROLE_TO_ASSUME is the value for the secret of
    the same name, used by both workflows. APP_ONLY_ROLE_ARN is the
    least-privilege deploy role, only needed if the App workflow is later given
    its own role secret.
  EOT
  value = {
    for env in local.envs : env => {
      AWS_ROLE_TO_ASSUME = aws_iam_role.iac[env].arn
      APP_ONLY_ROLE_ARN  = aws_iam_role.app[env].arn
    }
  }
}
