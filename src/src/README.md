# Sample Lambda application (`src/`)

This directory holds the **deployed** LangGraph + FastAPI agent Lambda. The
original SNS→SQS Bedrock reference sample (`handler.py`, `whatsapp_lambda.py`)
has moved to `../old/` and is no longer part of this app — its old environment
variables (`BEDROCK_ENABLED`, `BEDROCK_MODE`, `BEDROCK_AGENT_ID`,
`BEDROCK_AGENT_ALIAS`, `DYNAMODB_TABLE_NAME`, `RPA_API_ENDPOINT`,
`RPA_SECRET_ARN`) do **not** apply here.

> **No `BEDROCK_MODE`.** The old sample had a `model`-vs-`agent` switch because
> it could call either a foundation model or a Bedrock Agent. The deployed app
> does neither of those directly: it drives the model through LangGraph with
> `ChatBedrockConverse`, so there is no mode to choose. The only Bedrock setting
> is `BEDROCK_MODEL_ID` (the model / inference-profile id). See the environment
> table below for the complete, current list.

---

# LangGraph FastAPI agent Lambda

This directory also contains the **deployed** agent: a LangGraph ordering agent
served through FastAPI and run on Lambda via Mangum. It greets the user, then
authorizes them through the `verify_user` tool — the model extracts the ID from
natural language (e.g. "my id is U1001") and the tool checks it against the
users table, returning `authorized` plus any stored profile (name/email/
address). Until `verify_user` authorizes, every other tool is blocked. Once
authorized, the agent confirms any stored ship-to fields with the user and asks
only for the missing ones, collects the products and quantity, then shows the
full order and asks the user to answer **yes or no** before placing it. It
places the order through the RPA endpoint and (on the callback) notifies the
user over WhatsApp while recording chat history.

**Profile prefill:** filled profile fields from the users table are shown for
confirmation (not re-asked); empty/missing ones are asked for. Edits the user
gives apply to this order only and are **not** written back to the users table.

**Confirm step:** the order summary is shown and the user must reply yes/no — a
clear yes places the order, a clear no cancels, and any ambiguous reply re-asks
rather than silently cancelling.

Authorization persists across turns: a follow-up message never resets it (only
a brand-new conversation thread starts unauthorized).

The original SNS→SQS reference sample (`handler.py` and `whatsapp_lambda.py`)
has been moved to `../old/` and is kept for reference only. It is *not* part of
the deployed app. The WhatsApp send/extract logic now lives directly in
`whatsapp.py` (self-contained — it no longer imports from `whatsapp_lambda.py`).

## Modules

| File             | Role                                                                    |
| ---------------- | ----------------------------------------------------------------------- |
| `app.py`         | FastAPI app, CORS, `GET /`, `POST /ask`, `POST /callback`, `lambda_handler` dispatcher + SQS path |
| `agent.py`       | LangGraph graph, `verify_user` / `get_current_time` / `create_order` tools, auth gate, HITL interrupt, `run_agent` |
| `checkpointer.py`| `DynamoDBSaver(BaseCheckpointSaver)` — LangGraph state persistence       |
| `data.py`        | Env-driven access to the four DynamoDB tables                           |
| `whatsapp.py`    | Self-contained WhatsApp send/extract helpers (End User Messaging Social) |
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
Compress-Archive -Path app.py,agent.py,checkpointer.py,data.py,whatsapp.py,test_page.html `
  -DestinationPath function.zip -CompressionLevel Optimal
```

The old reference files (`handler.py`, `whatsapp_lambda.py`) now live in
`../old/` and are **not** part of the deployment. `whatsapp.py` is
self-contained, so nothing else is needed.

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

**Users table — optional profile attributes.** Only `user_id` (the key) is
required. Each user item may also carry a stored profile that the agent uses to
prefill the order's ship-to details (and confirm with the user rather than
asking from scratch):

| Attribute      | Type | Used for                                     |
| -------------- | ---- | -------------------------------------------- |
| `user_id`      | S    | key — authorization                          |
| `user_name`    | S    | prefilled ship-to name (optional)            |
| `user_email`   | S    | prefilled ship-to email (optional)           |
| `user_address` | S    | prefilled ship-to address (optional)         |

A filled profile field is shown to the user for confirmation; a missing/empty
one is asked for normally. Example item:

```json
{
  "user_id": "U1001",
  "user_name": "Ada Lovelace",
  "user_email": "ada@example.com",
  "user_address": "1 Analytical Engine Way"
}
```

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
