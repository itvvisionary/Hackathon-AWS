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
| `logging_setup.py`| Shared log config (format: time/level/file/func/line) + `@log_io` input/output debug decorator |
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

## HTTP endpoints

| Method & path  | Request body                                                        | Response            |
| -------------- | ------------------------------------------------------------------- | ------------------- |
| `GET /`        | —                                                                   | the test page HTML  |
| `POST /ask`    | `{ "conversation_id": str, "message": str }`                        | `{ "reply", "conversation_id" }` |
| `POST /callback` | `{ "conversation_id": str, "receipt_id": str, "total_cost": any }` | `{ "status": "ok", "channel" }` |
| `GET /history?conversation_id=…` | query param `conversation_id`                     | `{ "conversation_id", "turns": [{role, text, created_at}] }` |

**Callback matching.** The mapping table is keyed on `conversation_id`, and the
callback is matched on that alone — it sends only `conversation_id`,
`receipt_id`, and `total_cost`. The hook loads the mapping, reads the stored
`workflow_id` (recorded by `create_order` from the RPA response `id`), and puts
it in the completion message: *"Here are the results for workflow &lt;id&gt;:
Receipt …, Total cost …"*. A missing mapping returns **404**.

**Channel routing.** `create_order` records a `channel` in the mapping —
`"whatsapp"` when the conversation arrived over SQS/WhatsApp (a sender number is
present), or `"api"` for the HTTP `/ask` (UI) path. On completion the callback
routes by that channel: `whatsapp` pushes the message to the user's WhatsApp
number; `api` only stores the message in chat history (a browser can't receive a
server push), and the UI retrieves it with **`GET /history`** — the test page's
**Load history** button does exactly this. Both channels always persist the
completion turn to the chat-history table.

**Both triggers persist to DynamoDB.** Whether a turn arrives via `POST /ask` or
via the SQS/WhatsApp path, the user message and the assistant reply are both
written to the chat-history table, so WhatsApp conversations are stored the same
way as UI ones.

## SQS / WhatsApp payload

The SQS path handles inbound WhatsApp webhooks (typically `SNS → SQS → Lambda`).
Unlike the HTTP endpoints, **there is no explicit `conversation_id` field** in
the payload — the code uses the sender's phone number (the message `from` field)
as *both* the `conversation_id` and the `whatsapp_number`:

```python
# app.py, SQS path
for msg in whatsapp.extract_text_messages(webhook):
    sender = msg["sender"]                     # <- the "from" field
    reply = agent.run_agent(sender, msg["text"], whatsapp_number=sender)
    #                        ^^^^^^ conversation_id      ^^^^^^ whatsapp_number
```

So the phone number identifies the conversation and is where the callback later
sends the completion message.

### Webhook message (what SNS publishes)

Publish this as the SNS message; it becomes the `Message` field of the SQS body.
The **`from` field (here `201001234567`) is the conversation_id**:

```json
{
  "whatsAppWebhookEntry": [
    {
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "messages": [
              {
                "from": "201001234567",
                "id": "wamid.TESTMESSAGE0001",
                "type": "text",
                "text": { "body": "hi, my id is U1001" }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

- `from` → becomes `conversation_id` **and** `whatsapp_number`.
- `type` must be `"text"` (status/non-text messages are skipped).
- `text.body` is the user's message passed to the agent.

### Full Lambda event (SNS → SQS)

What the Lambda actually receives — useful for a direct Lambda test too. The
`body` is a JSON **string** (the SNS envelope), whose `Message` is itself a JSON
**string** (the webhook above); the extractor unwraps both:

```json
{
  "Records": [
    {
      "messageId": "11111111-1111-1111-1111-111111111111",
      "eventSource": "aws:sqs",
      "body": "{\"Type\":\"Notification\",\"Message\":\"{\\\"whatsAppWebhookEntry\\\":[{\\\"changes\\\":[{\\\"value\\\":{\\\"messaging_product\\\":\\\"whatsapp\\\",\\\"messages\\\":[{\\\"from\\\":\\\"201001234567\\\",\\\"id\\\":\\\"wamid.TESTMESSAGE0001\\\",\\\"type\\\":\\\"text\\\",\\\"text\\\":{\\\"body\\\":\\\"hi, my id is U1001\\\"}}]}}]}]}\"}"
    }
  ]
}
```

If you publish straight to SQS (no SNS), set `body` to just the webhook JSON
string — the extractor handles both (it only unwraps when it sees a `Message`
field). The dispatcher routes an event to this path when
`Records[0].eventSource == "aws:sqs"`.

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
Compress-Archive -Path app.py,agent.py,checkpointer.py,data.py,whatsapp.py,logging_setup.py,test_page.html `
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
| `LOG_LEVEL`                | no       | Log verbosity: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. Set `DEBUG` to log every endpoint/function's input and output |

## Logging

All modules log through `logging_setup.py`, which formats every line as:

```
<time> <LEVEL> [req=<request_id> ep=<endpoint_request_id>] [<filename>:<function>:<line>] <message>
```

**Correlation ids.** Every log line carries two ids so you can trace a request:

- `request_id` — the Lambda invocation id (`context.aws_request_id`), set once
  per invocation in `lambda_handler`. Stable across all logs of that invocation.
- `endpoint_request_id` — a per-call id set at the start of each endpoint
  (`/ask`, `/callback`, `/history`) and per SQS record (its `messageId`). Unique
  to that single endpoint/message so you can isolate one request's logs within a
  warm container that served several.

Both default to `-` until set. They're stored in `contextvars` and injected by a
logging filter, so they appear on **every** line in the call chain (including
deep in `data.py`/`agent.py`) without any per-call code.

`LOG_LEVEL` (default `INFO`) controls verbosity. At `DEBUG`, each endpoint and
key function logs its **input** and **output** data:

- Endpoints (`/ask`, `/callback`, `/history`) and the SQS path log the request
  and the composed response.
- Core functions carry a `@log_io` decorator that logs call arguments and the
  return value, attributed to the real file/function/line (e.g.
  `[agent.py:place_order:210] -> input: ...`).
- Long payloads are truncated so logs stay readable.

Example (`LOG_LEVEL=DEBUG`):

```
2026-10-21 14:57:37 DEBUG [req=abc-123 ep=5f2a1c] [agent.py:run_agent:430] -> input: args=('conv-1', 'hi') kwargs={}
2026-10-21 14:57:37 DEBUG [req=abc-123 ep=5f2a1c] [data.py:append_turn:88] -> input: args=('conv-1', 'user', 'hi') kwargs={}
2026-10-21 14:57:37 DEBUG [req=abc-123 ep=5f2a1c] [agent.py:run_agent:430] <- output: 'Welcome! ...'
```

## DynamoDB tables

Four tables back the agent. Names come from the env vars above; keys and
attributes are below. All tables work fine with **on-demand (PAY_PER_REQUEST)**
capacity. Only the **users** table needs manual data (see *Seeding dummy data*);
the agent writes the other three at runtime.

### Key schemas (create these in the console)

| Table (env var)           | Partition key (HASH)    | Sort key (RANGE) | TTL              |
| ------------------------- | ----------------------- | ---------------- | ---------------- |
| `USERS_TABLE_NAME`        | `user_id` (S)           | —                | —                |
| `CHAT_HISTORY_TABLE_NAME` | `conversation_id` (S)   | `sk` (S)         | —                |
| `CHECKPOINTER_TABLE_NAME` | `pk` (S)                | `sk` (S)         | **`expires_at`** |
| `MAPPING_TABLE_NAME`      | `conversation_id` (S)   | —                | —                |

Enable **TTL on the `expires_at` attribute** of the checkpointer table so
LangGraph checkpoints expire automatically (items are written with
`expires_at = now + 7 days`).

### 1. Users table (`USERS_TABLE_NAME`)

Authorization + optional stored profile. Only `user_id` (the key) is required;
the profile fields are optional — a filled one is confirmed with the user, a
missing one is asked for.

| Attribute      | Type | Required | Notes                                      |
| -------------- | ---- | -------- | ------------------------------------------ |
| `user_id`      | S    | yes      | partition key; the ID the user types       |
| `user_name`    | S    | no       | prefilled ship-to name                     |
| `user_email`   | S    | no       | prefilled ship-to email                    |
| `user_address` | S    | no       | prefilled ship-to address                  |

```json
{
  "user_id": "U1001",
  "user_name": "Ada Lovelace",
  "user_email": "ada@example.com",
  "user_address": "1 Analytical Engine Way"
}
```

### 2. Chat history table (`CHAT_HISTORY_TABLE_NAME`)

Written by the app (`data.append_turn`) on every user and assistant turn. You
do not seed this.

| Attribute         | Type | Notes                                             |
| ----------------- | ---- | ------------------------------------------------- |
| `conversation_id` | S    | partition key                                     |
| `sk`              | S    | sort key — `"<epoch_ms>-<seq>-<uuid8>"`, chronological |
| `role`            | S    | `"user"` or `"assistant"`                         |
| `text`            | S    | the message text                                  |
| `created_at`      | N    | epoch seconds                                     |

```json
{
  "conversation_id": "conv-ab12cd34",
  "sk": "1760000000123-000042-a1b2c3d4",
  "role": "assistant",
  "text": "Your order is complete. Receipt RCPT-9. Total cost 42.50.",
  "created_at": 1760000000
}
```

### 3. Checkpointer table (`CHECKPOINTER_TABLE_NAME`)

Managed entirely by `DynamoDBSaver` (LangGraph state). You create the table
(keys + TTL) but never write items by hand.

| Attribute              | Type | Notes                                              |
| ---------------------- | ---- | -------------------------------------------------- |
| `pk`                   | S    | partition key — `"<thread_id>::<checkpoint_ns>"` (thread_id = `conversation_id`) |
| `sk`                   | S    | sort key — `"checkpoint::<id>"` or `"write::<id>::<task>::<idx>"` |
| `expires_at`           | N    | **TTL attribute** — now + 7 days                   |
| `checkpoint`, `metadata`, `type`, `channel`, `task_id`, … | — | serialized state written by the saver |

### 4. Mapping table (`MAPPING_TABLE_NAME`)

Written by `create_order` (`data.put_mapping`) and read by `POST /callback`. You
do not seed this.

| Attribute         | Type | Notes                                              |
| ----------------- | ---- | -------------------------------------------------- |
| `conversation_id` | S    | partition key                                      |
| `workflow_id`     | S    | the RPA response `id`, stored as a string; the callback must echo it |
| `user_id`         | S    | the verified user                                  |
| `whatsapp_number` | S    | WhatsApp recipient (empty for the API/UI channel)  |
| `channel`         | S    | `"whatsapp"` or `"api"` — how the order was placed; the callback routes the completion the same way |

```json
{
  "conversation_id": "conv-ab12cd34",
  "workflow_id": "237436573",
  "user_id": "U1001",
  "whatsapp_number": "+201001234567",
  "channel": "whatsapp"
}
```

## Seeding dummy data

Only the **users** table needs seeding — it is the authorization source. The
chat-history, checkpointer, and mapping tables are populated automatically by
the app at runtime, so leave them empty.

### Option A — AWS Console

1. Open the users table → **Explore table items** → **Create item**.
2. Switch to **JSON** view and paste an item (add/remove profile fields as you
   like — only `user_id` is required):

   ```json
   {
     "user_id": "U1001",
     "user_name": "Ada Lovelace",
     "user_email": "ada@example.com",
     "user_address": "1 Analytical Engine Way"
   }
   ```
3. **Create item**. Repeat for each test user. The ID the user types in chat
   must match `user_id` **exactly** (it is case-sensitive, trimmed only).

### Option B — AWS CLI (`put-item`)

Replace `<users-table>` with your actual table name:

```bash
aws dynamodb put-item --table-name <users-table> --item '{
  "user_id":      {"S": "U1001"},
  "user_name":    {"S": "Ada Lovelace"},
  "user_email":   {"S": "ada@example.com"},
  "user_address": {"S": "1 Analytical Engine Way"}
}'

# A user with no stored profile (the agent will ask for name/email/address):
aws dynamodb put-item --table-name <users-table> --item '{
  "user_id": {"S": "U1002"}
}'
```

### Option C — AWS CLI (`batch-write-item`)

Create `users-seed.json`:

```json
{
  "<users-table>": [
    { "PutRequest": { "Item": {
      "user_id":      {"S": "U1001"},
      "user_name":    {"S": "Ada Lovelace"},
      "user_email":   {"S": "ada@example.com"},
      "user_address": {"S": "1 Analytical Engine Way"}
    }}},
    { "PutRequest": { "Item": {
      "user_id":      {"S": "U1002"},
      "user_name":    {"S": "Alan Turing"},
      "user_email":   {"S": "alan@example.com"}
    }}}
  ]
}
```

Then:

```bash
aws dynamodb batch-write-item --request-items file://users-seed.json
```

> Note: `U1002` above omits `user_address`, so the agent will ask for the
> address during ordering while prefilling the name and email.

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
