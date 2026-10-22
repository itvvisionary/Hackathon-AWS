# environments/dev/backend.hcl
# Purpose: dev state file path inside the bucket set in backend.tf.
#   terraform init -backend-config=environments/dev/backend.hcl
key = "wa-ai-rpa/dev/terraform.tfstate"
