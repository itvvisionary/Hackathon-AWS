# environments/prod/backend.hcl
# Purpose: prod state file path inside the bucket set in backend.tf.
#   terraform init -backend-config=environments/prod/backend.hcl
key = "wa-ai-rpa/prod/terraform.tfstate"
