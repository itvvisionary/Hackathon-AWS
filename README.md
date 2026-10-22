# terraform-whatsapp-ai-rpa

WhatsApp AI-agent + RPA pipeline on AWS:

```
Customer → WhatsApp → Amazon End User Messaging Social → SNS → SQS → Lambda → Amazon Bedrock → RPA
                                                                      │
                                                                      └── Amazon DynamoDB (users, mapping, checkpoints, chat history)
```

The repository has two independent parts, each with its own GitHub Actions
pipeline that runs only when its folder changes:

| Folder | Contains | Pipeline | Runs on push to `main` when |
| --- | --- | --- | --- |
| `IaC/` | Terraform: SNS, SQS, DynamoDB, Lambda configuration, IAM, artifacts bucket | `.github/workflows/iac.yml` | `IaC/**` changes |
| `App/` | Lambda code and its dependency layer | `.github/workflows/app.yml` | `App/**` changes |

**Ownership split.** Terraform owns the Lambda's *configuration* (runtime,
handler, memory, timeout, role, environment variables). The App pipeline owns
its *code* and *layers*. Terraform creates the function with a placeholder
package and ignores code and layer changes afterwards
(`lifecycle.ignore_changes`), so an IaC run never rolls back the app, and the
App pipeline never changes environment variables.

---

## 1. Project structure

```
.
├── .github/workflows/
│   ├── iac.yml                 # Terraform plan/apply (paths: IaC/**)
│   └── app.yml                 # Layer + code deploy  (paths: App/**)
├── IaC/
│   ├── backend.tf              # S3 remote state (partial config)
│   ├── main.tf  variables.tf  outputs.tf  locals.tf  providers.tf  versions.tf
│   ├── terraform.tfvars.example
│   ├── environments/
│   │   ├── dev/  terraform.tfvars  backend.hcl
│   │   └── prod/ terraform.tfvars  backend.hcl
│   ├── modules/
│   │   ├── messaging/              # SNS topic + policy
│   │   ├── message-queue/          # SQS, DLQ, SNS subscription
│   │   ├── conversation-database/  # one DynamoDB table (used 4x)
│   │   ├── ai-agent/               # Bedrock IAM policy
│   │   └── message-processor/      # Lambda, role, SQS trigger, Function URL
│   └── bootstrap/              # one-time: GitHub OIDC + CI roles
└── App/
    ├── HackathonWhatsAppCode/  # Lambda code (handler app.lambda_handler)
    ├── layer/requirements.txt  # third-party libraries -> Lambda layer
    └── scripts/build_layer.py  # builds the layer zip (Linux wheels)
```

---

## 2. What the pipelines do

### IaC (`iac.yml`)

| Trigger | Result |
| --- | --- |
| Pull request touching `IaC/**` | `fmt -check`, `validate`, `plan` against dev |
| Push to `main` touching `IaC/**` | plan + apply to dev |
| Manual run (Actions → IaC → Run workflow) | plan or apply for dev or prod |

Changes under `IaC/bootstrap/` do not trigger it (bootstrap is applied by hand).

### App (`app.yml`)

On push to `main` touching `App/**`, or a manual run for dev/prod:

1. Reads the function's runtime and architecture (set by Terraform).
2. Computes a fingerprint of `layer/requirements.txt` + `build_layer.py` +
   runtime + architecture. If a layer version with that fingerprint already
   exists it is reused; otherwise the layer is built, uploaded to the
   artifacts bucket and published from S3 (avoids the request-signature
   expiry of a ~48 MB direct upload).
3. Attaches the layer, replacing older versions of it and keeping any other layers.
4. Zips `HackathonWhatsAppCode/` (no `__pycache__`/tests) and updates the code.

A code-only change takes about a minute; a dependency change adds the layer build.

---

## 3. One-time setup

### 3.1 Prerequisites

- Terraform **1.10 or newer** locally (S3 native state locking). The pipelines use 1.17.0.
- AWS CLI with admin credentials for the target account.
- In **Bedrock console → Model access**, enable the model in `bedrock_model_id`.

### 3.2 Bootstrap: GitHub OIDC and CI roles

Terraform state lives in the existing bucket **`state-file-hackathon-aws`**
(`us-east-1`), one key per environment:
`wa-ai-rpa/dev/terraform.tfstate` and `wa-ai-rpa/prod/terraform.tfstate`
(see `IaC/environments/<env>/backend.hcl`). Turn on versioning for that bucket
so an overwritten or corrupted state can be recovered:

```powershell
aws s3api put-bucket-versioning --bucket state-file-hackathon-aws --versioning-configuration Status=Enabled
```

```powershell
cd IaC/bootstrap
Copy-Item terraform.tfvars.example terraform.tfvars   # set github_repository = "owner/name"
terraform init
terraform apply
terraform output
```

This creates, using local state in `IaC/bootstrap` (keep that state file):

- GitHub OIDC provider. Set `create_github_oidc_provider = false` if the account already has one.
- Per environment, two roles that only the matching GitHub Environment of your repo can assume:
  - `wa-ai-rpa-<env>-github-iac`: AdministratorAccess, because Terraform creates IAM roles, Lambda, SQS and so on.
  - `wa-ai-rpa-<env>-github-app`: least privilege. It can update only that environment's function, publish its layer and write `layers/*` in its artifacts bucket.


### 3.3 GitHub Environments

In the repository: **Settings → Environments**, create `dev` and `prod`, and add:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `AWS_REGION` | `us-east-1` |
| Variable | `IAC_ROLE_ARN` | bootstrap output `github_environment_variables.<env>.IAC_ROLE_ARN` |
| Variable | `APP_ROLE_ARN` | bootstrap output `github_environment_variables.<env>.APP_ROLE_ARN` |
| Variable | `LAMBDA_FUNCTION_NAME` | `wa-ai-rpa-<env>-message-processor` |
| Variable | `LAYER_NAME` | `wa-ai-rpa-<env>-langgraph` |
| Variable | `ARTIFACTS_BUCKET` | `wa-ai-rpa-<env>-lambda-artifacts-<account>` |
| Secret | `ORDER_API_KEY` | UiPath order API key |

After the first IaC apply, `terraform output app_deploy_config` prints the last
four values exactly. For `prod`, add **Required reviewers** so applies and
deploys wait for approval.

### 3.4 First deployment order

1. Push `IaC/` (or run the IaC workflow): creates everything; the Lambda runs a placeholder.
2. Run the App workflow (Actions → App → Run workflow): deploys the layer and code.

Until step 2 the placeholder fails every invocation on purpose, so SQS keeps
messages instead of losing them. If one push changes both folders, both
workflows start together; if App runs before the function exists, re-run it
after IaC finishes.

### 3.5 Connect WhatsApp (manual, not Terraform-provisionable)

1. AWS Console → **End User Messaging → Social**: link the WhatsApp Business
   Account through Meta's sign-up.
2. Put the phone number ID (`phone-number-id-...`) in
   `whatsapp_phone_number_id` in `IaC/environments/<env>/terraform.tfvars` and push.
3. Set the WABA **event destination** to `terraform output sns_topic_arn`.

---

## 4. Running Terraform locally

```powershell
cd IaC
$env:TF_VAR_order_api_key = "<key>"   # never put it in a tfvars file
terraform init -backend-config=environments/dev/backend.hcl
terraform plan -var-file=environments/dev/terraform.tfvars
```

Local runs and the pipeline share the same S3 state and lock. `ORDER_API_KEY`
is marked sensitive but is stored in the state and the Lambda configuration,
so keep the state bucket private.

To build the layer locally (for inspection only; the pipeline publishes it):

```powershell
cd App
python scripts/build_layer.py   # -> App/layer/dist/langgraph-layer.zip
```

---

## 5. Changing things

| You want to | Edit | Pipeline |
| --- | --- | --- |
| Change agent / app behaviour | `App/HackathonWhatsAppCode/*` | App (code only) |
| Add or upgrade a Python library | `App/layer/requirements.txt` | App (new layer + code) |
| Change memory, timeout, env vars, tables, queues | `IaC/**` | IaC |
| Add an environment variable the app reads | `IaC/main.tf` (`environment_variables`) | IaC first, then App |

---

## 6. Testing the flow

1. Add a test user. `user_name`, `user_email` and `user_address` are optional;
   when present the agent offers them for confirmation:
   ```powershell
   aws dynamodb put-item --table-name hackathon_user_table --item '{\"user_id\":{\"S\":\"U1001\"},\"user_name\":{\"S\":\"Ali\"},\"user_email\":{\"S\":\"ali@example.com\"},\"user_address\":{\"S\":\"Cairo\"}}'
   ```
2. Send a WhatsApp message to the linked number ("hi, my id is U1001").
3. Watch the Lambda logs in CloudWatch (`/aws/lambda/wa-ai-rpa-dev-message-processor`).

Without WhatsApp (dev only), open `App/HackathonWhatsAppCode/test_page.html`
and paste `terraform output -raw lambda_function_url` as the base URL. The RPA
platform reports completion with `POST <url>/callback` and body
`{"conversation_id": "...", "receipt_id": "...", "total_cost": ...}`.

The Function URL is public (auth `NONE`) and the app does not authenticate
requests: anyone with the URL can chat with the agent or post a fake
completion that triggers a WhatsApp message. It is disabled in prod.

`lambda_log_level = "DEBUG"` (dev) logs every function's inputs and outputs,
including customer names, emails and addresses. Prod uses `INFO`.
