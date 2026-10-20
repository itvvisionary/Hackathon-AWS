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

Everything in `requirements.txt` **except boto3** goes in the layer (boto3 is
bundled in the Python 3.12 runtime):

```bash
pip install -r requirements.txt -t python/
# boto3 is runtime-bundled — remove it so it does not ship in the layer:
rm -rf python/boto3 python/botocore python/boto3*.dist-info python/botocore*.dist-info
zip -r layer.zip python
```

The deployment **zip** itself contains only the `.py` modules
(`app.py`, `agent.py`, `checkpointer.py`, `data.py`, `whatsapp.py`,
and the reference `handler.py` / `whatsapp_lambda.py`) plus `test_page.html`;
the third-party dependencies ship as the layer above.

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
