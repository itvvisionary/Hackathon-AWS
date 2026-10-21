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

---

# LangGraph FastAPI agent Lambda

This directory also contains the **deployed** agent: a LangGraph ordering agent
served through FastAPI and run on Lambda via Mangum. It greets the user, checks
the typed ID against the users table, collects the order parameters, interrupts
to confirm, places the order through the RPA endpoint, and (on the callback)
notifies the user over WhatsApp while recording chat history.

`handler.py` and `whatsapp_lambda.py` remain in this directory as **reference
only** — they are the original SNS→SQS sample. They are *not* the deployed
entry point and are left unchanged. The agent's helpers import the proven
WhatsApp send/extract functions from `whatsapp_lambda.py` verbatim.

## Modules

| File             | Role                                                                    |
| ---------------- | ----------------------------------------------------------------------- |
| `app.py`         | FastAPI app, CORS, `GET /`, `POST /ask`, `POST /callback`, `lambda_handler` dispatcher + SQS path |
| `agent.py`       | LangGraph graph, `get_current_time` / `create_order` tools, HITL interrupt, `run_agent` |
| `checkpointer.py`| `DynamoDBSaver(BaseCheckpointSaver)` — LangGraph state persistence       |
| `data.py`        | Env-driven access to the four DynamoDB tables                           |
| `whatsapp.py`    | Thin re-export of `whatsapp_lambda.py` helpers                          |
| `test_page.html` | Vanilla-JS local test UI (opened from `file://`)                        |

## Lambda handler path

Set the Lambda handler to:

```
app.lambda_handler
```

`lambda_handler(event, context)` routes SQS events
(`event["Records"][0]["eventSource"] == "aws:sqs"`) to the WhatsApp/SQS batch
path and everything else to Mangum (the HTTP / Function URL path). This lets a
single Lambda serve both the `/ask` Function URL and direct SQS invocations.

## Function URL

Enable a **Lambda Function URL** and make it **public** (auth type `NONE`). The
`test_page.html` page is opened from `file://`, so the app sets permissive CORS
(`allow_origins=["*"]`) and the Function URL must allow unauthenticated access
for the browser test flow to reach `/ask` and `/callback`.

## Building the layer

The layer carries everything in `requirements.txt` **except boto3** (boto3 is
bundled in the Python 3.12 runtime). The dependencies include native wheels
(`pydantic-core`, `numpy`, `orjson`, ...), so the layer **must be built against
the Linux x86_64 runtime** — a plain `pip install` on Windows/macOS produces the
wrong binaries. Build it inside the official AWS Lambda image with Docker.

### Quick build (Windows / PowerShell)

```powershell
# from src/src
docker run --rm --entrypoint /bin/bash `
  -v "${PWD}:/var/task" public.ecr.aws/lambda/python:3.12 `
  -c "pip install --no-cache-dir -r requirements-layer.txt -t python/"
# strip the runtime-bundled boto3 stack + caches and zip to layer.zip:
powershell -ExecutionPolicy Bypass -File .\build_layer.ps1
```

`requirements-layer.txt` is `requirements.txt` without boto3. `build_layer.ps1`
removes the bundled `boto3`/`botocore`/`s3transfer`/`jmespath`, strips
`__pycache__`, and produces `layer.zip` (packages under a top-level `python/`
directory, as Lambda requires).

### Equivalent on macOS / Linux

```bash
docker run --rm --entrypoint /bin/bash \
  -v "$PWD:/var/task" public.ecr.aws/lambda/python:3.12 \
  -c "pip install --no-cache-dir -r requirements-layer.txt -t python/"
rm -rf python/boto3* python/botocore* python/s3transfer* python/jmespath*
zip -r layer.zip python
```

Resulting sizes: **~104 MB unzipped / ~29 MB zipped**, well under Lambda's
250 MB unzipped limit.

## Building the function zip

The deployment **zip** contains only the `.py` modules plus `test_page.html`;
the third-party dependencies come from the layer above.

```powershell
# from src/src
Compress-Archive -Path app.py,agent.py,checkpointer.py,data.py,whatsapp.py,whatsapp_lambda.py,test_page.html `
  -DestinationPath function.zip -CompressionLevel Optimal
```

`handler.py` is the old SQS reference and is **not** part of the new app, so it
is omitted; `whatsapp_lambda.py` is included because `whatsapp.py` re-exports
its helpers.

## Uploading to Lambda

Console route:

1. **Lambda → Layers → Create layer** → upload `layer.zip`, runtime
   *Python 3.12*, architecture *x86_64* → note the layer ARN/version.
2. **Lambda → your function → Code** → *Upload from → .zip file* → upload
   `function.zip`.
3. **Function → Layers → Add a layer → Custom layers** → pick the layer above.
4. **Runtime settings → Handler** = `app.lambda_handler`.
5. Set the environment variables (below) and enable a public **Function URL**.

CLI route (if the zips are &gt;50 MB, upload via S3 first):

```bash
aws lambda publish-layer-version --layer-name langgraph-agent-deps \
  --zip-file fileb://layer.zip --compatible-runtimes python3.12 \
  --compatible-architectures x86_64
aws lambda update-function-code --function-name <fn> --zip-file fileb://function.zip
```

`requirements-dev.txt` adds `pytest` and `moto` for the offline test suite.
Those are **dev-only** and must never be packaged into the layer or the zip.

## Environment variables

| Variable                   | Required | Purpose                                                            |
| -------------------------- | -------- | ------------------------------------------------------------------ |
| `BEDROCK_MODEL_ID`         | yes      | Bedrock model / inference-profile id for `ChatBedrockConverse`     |
| `USERS_TABLE_NAME`         | yes      | Authorized-users table                                             |
| `CHAT_HISTORY_TABLE_NAME`  | yes      | Conversation-turns table                                           |
| `CHECKPOINTER_TABLE_NAME`  | yes      | LangGraph checkpointer table (needs TTL — see below)               |
| `MAPPING_TABLE_NAME`       | yes      | conversation → workflow mapping table                              |
| `ORDER_API_ENDPOINT`       | yes      | RPA order-start endpoint (HTTP POST)                               |
| `ORDER_API_KEY`            | no       | Bearer/api key for the order endpoint; header added only when set  |
| `WHATSAPP_PHONE_NUMBER_ID` | no       | Origination phone-number id for the `socialmessaging` send; outbound WhatsApp is gated on this being present |
| `META_API_VERSION`         | no       | Meta API version (default `v20.0`)                                 |
| `AWS_REGION`               | no       | AWS region (default `us-east-1`)                                   |

## DynamoDB tables

Four tables back the agent. Create them with these key schemas:

| Table (env var)           | Partition key (HASH)    | Sort key (RANGE) | TTL                 |
| ------------------------- | ----------------------- | ---------------- | ------------------- |
| `USERS_TABLE_NAME`        | `user_id` (S)           | —                | —                   |
| `CHAT_HISTORY_TABLE_NAME` | `conversation_id` (S)   | `sk` (S)         | —                   |
| `CHECKPOINTER_TABLE_NAME` | `pk` (S)                | `sk` (S)         | **`expires_at`**    |
| `MAPPING_TABLE_NAME`      | `conversation_id` (S)   | —                | —                   |

Enable **TTL on the `expires_at` attribute** of the checkpointer table so
LangGraph checkpoints expire automatically (items are written with
`expires_at = now + 7 days`).

## Local testing

```bash
pip install -r requirements-dev.txt
python -m pytest -q    # fully offline: Bedrock, DynamoDB, requests, and WhatsApp are mocked
```

Then open `test_page.html` in a browser, paste the Function URL, and exercise
`/ask` (type `yes` at the confirm prompt) and `/callback`.

> **Dependency versions:** `requirements.txt` pins the `langgraph` 1.x line that
> the code was built and verified against, not the 0.2.x line sketched in the
> planning notes. Keep `langgraph` and `langgraph-checkpoint` on matching
> majors and re-run the full offline suite before changing either.
