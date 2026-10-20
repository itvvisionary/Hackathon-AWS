# Sample Lambda application (`src/`)

This is a **reference** message-processor for the pipeline:

```
SNS -> SQS -> (this Lambda) -> DynamoDB + Amazon Bedrock -> external RPA
```

## Packaging

The Terraform `message-processor` module zips this directory automatically at
plan time (via the `archive` provider) when `lambda_package_path` is empty and
`lambda_source_dir` points here. No manual build step is required for the sample.

For a production build with third-party dependencies, build your own artifact
and set `lambda_package_path` to its `.zip`:

```bash
# from the project root
pip install -r src/requirements.txt -t build/
cp src/handler.py build/
cd build && zip -r ../lambda.zip . && cd ..
# then: lambda_package_path = "lambda.zip"
```

## Dependencies

The sample uses only `boto3` (bundled in the Lambda runtime) and the Python
standard library, so `requirements.txt` is intentionally empty.

## Configuration (environment variables, set by Terraform)

| Variable              | Purpose                                             |
| --------------------- | --------------------------------------------------- |
| `DYNAMODB_TABLE_NAME` | Conversation table name                             |
| `BEDROCK_ENABLED`     | `true`/`false` master switch                        |
| `BEDROCK_MODE`        | `model` or `agent`                                  |
| `BEDROCK_MODEL_ID`    | Foundation/inference-profile model ID (model mode)  |
| `BEDROCK_AGENT_ID`    | Agent ID (agent mode)                               |
| `BEDROCK_AGENT_ALIAS` | Agent alias ID (agent mode)                         |
| `RPA_API_ENDPOINT`    | External RPA HTTP endpoint                          |
| `RPA_SECRET_ARN`      | Secrets Manager ARN holding RPA credentials         |
| `LOG_LEVEL`           | Python log level                                    |

## What you must customise

- `_extract_message` — map your actual WhatsApp webhook payload shape.
- `_invoke_bedrock_model` — adjust the request body for your chosen model family.
- `invoke_rpa` — implement the real RPA call/contract.
