# Implementation Plan — LangGraph + FastAPI Lambda (dual trigger: HTTP Function URL + SQS/WhatsApp)

Working directory (worktree, all code here): `d:\GenAI Projects\AWS Hackathon\AWS CDS Agentic AI Partner Hackathon\src\src`
Artifacts/plan (never committed): `...\src\src\.agents\tasks`

Leave `handler.py` and `whatsapp_lambda.py` untouched (reference). New code lives alongside them. Target Python 3.12.

## Design decisions (made here, grounded in the repo)

- **New modules, don't edit references.** `whatsapp_lambda.py` already has proven `extract_webhook_data`, `extract_text_messages`, `normalize_destination`, `send_whatsapp_message` using the `socialmessaging` boto3 client with env `WHATSAPP_PHONE_NUMBER_ID` / `META_API_VERSION`. We import these verbatim from `whatsapp_lambda` rather than re-implement, so WhatsApp behavior stays identical. `whatsapp.py` is a thin wrapper that re-exports them (keeps new code's imports stable even if references move).
- **Single Lambda, dual trigger.** `app.lambda_handler` dispatches: `event.get("Records") and Records[0].get("eventSource") == "aws:sqs"` → SQS/WhatsApp path; otherwise `Mangum(app)(event, context)` for HTTP. Both call one `run_agent(conversation_id, user_text) -> str`.
- **LLM via `langchain-aws` `ChatBedrockConverse`** (Bedrock Agents NOT used), model id from `BEDROCK_MODEL_ID` env (default a Claude inference-profile id). `.bind_tools([...])` for tool calling.
- **Custom DynamoDB checkpointer** subclassing `langgraph.checkpoint.base.BaseCheckpointSaver`, serializing with the provided serde, storing checkpoint + writes items in the checkpointer table, TTL `expires_at = now + 604800`. Thread id = `conversation_id`. We do NOT use `MemorySaver` on the production path (Lambda is stateless; the HITL interrupt must survive across invocations). Rationale: the task requires a real subclass; `langgraph-checkpoint-aws`'s DynamoDBSaver is avoided to keep full control of the four-table/TTL contract.
- **HITL via dynamic `interrupt(...)` + `Command(resume=...)`** (stable langgraph 0.2.x→v1 API). Before `create_order` runs, a confirm node calls `interrupt(payload)`. The graph pauses, checkpoint persists; next request resumes with `Command(resume=user_text)`. `run_agent` detects whether a thread has a pending interrupt (via `checkpointer.get_tuple` / graph state) and chooses `invoke(input)` vs `invoke(Command(resume=...))`.
- **conversation_id threading.** `conversation_id` is the graph thread id AND is passed into `create_order` args AND into the order API body, so the RPA echoes it back to `/callback`; `/callback` looks up `MAPPING_TABLE` by `conversation_id`. In the WhatsApp path `conversation_id == sender phone number`, which is also stored as `whatsapp_number` in the mapping. In the HTTP path `whatsapp_number` may be blank.

## Pinned package versions (Python 3.12, mutually compatible)

Verified on PyPI (Oct 2026). Using a mature, battle-tested set whose `interrupt`/`Command`/`BaseCheckpointSaver`/`ChatBedrockConverse` APIs match this plan:

```
fastapi==0.115.12
mangum==0.19.0
langgraph==0.2.76
langgraph-checkpoint==2.0.26
langchain-aws==0.2.19
langchain-core==0.3.63
pydantic==2.11.4
requests==2.32.3
boto3==1.35.99          # runtime-bundled; pinned for local/tests only
```

(Latest available are langgraph 1.2.x / langchain-aws 1.9.x / mangum 0.22.0; the pins above are deliberately a proven 0.2.x/0.3.x line. If the implementer prefers newest, langgraph>=1.0 keeps the same `interrupt`/`Command`/`BaseCheckpointSaver` APIs — but do NOT mix a v1 langgraph with a 0.2.x langgraph-checkpoint.)

## Environment variables (all names from env, nothing hardcoded)

`BEDROCK_MODEL_ID`, `USERS_TABLE_NAME`, `CHAT_HISTORY_TABLE_NAME`, `CHECKPOINTER_TABLE_NAME`, `MAPPING_TABLE_NAME`, `ORDER_API_ENDPOINT`, `ORDER_API_KEY` (optional), `WHATSAPP_PHONE_NUMBER_ID`, `META_API_VERSION` (default `v20.0`), `AWS_REGION`.

## Four DynamoDB tables (key schemas + access functions in `data.py`)

| Table (env)                | Keys                                      | Access functions |
| -------------------------- | ----------------------------------------- | ---------------- |
| `USERS_TABLE_NAME`         | PK `user_id` (S)                          | `is_authorized_user(user_id) -> bool` (get_item, return Item is not None) |
| `CHAT_HISTORY_TABLE_NAME`  | PK `conversation_id` (S), SK `sk` (S, `"{epoch_ms}-{uuid8}"`) | `append_turn(conversation_id, role, text)` |
| `CHECKPOINTER_TABLE_NAME`  | PK `pk` (S), SK `sk` (S) — see checkpointer design | used by `DynamoDBSaver` only |
| `MAPPING_TABLE_NAME`       | PK `conversation_id` (S)                  | `put_mapping(conversation_id, workflow_id, user_id, whatsapp_number)`, `get_mapping(conversation_id) -> dict|None` |

All use `boto3.resource("dynamodb")` at module scope, table handles cached. Decimal-safe (cast counts to `int`, costs to `str` in messages).

## DynamoDB checkpointer design (`checkpointer.py`, class `DynamoDBSaver(BaseCheckpointSaver)`)

Single checkpointer table, two item kinds distinguished by `sk` prefix. `thread_id = conversation_id`.

- **Checkpoint item**
  - `pk` = `f"{thread_id}::{checkpoint_ns}"`
  - `sk` = `f"checkpoint::{checkpoint_id}"`
  - `thread_id`, `checkpoint_ns`, `checkpoint_id` (S)
  - `parent_checkpoint_id` (S, optional)
  - `checkpoint` (B) = `self.serde.dumps_typed(checkpoint)` → store the tuple as `{"type": ..., "data": <binary>}` (store type string + bytes)
  - `metadata` (B) = `self.serde.dumps_typed(metadata)`
  - `expires_at` (N) = `int(time.time()) + 604800`
- **Writes item** (one per pending write)
  - `pk` = `f"{thread_id}::{checkpoint_ns}"`
  - `sk` = `f"write::{checkpoint_id}::{task_id}::{idx}"`
  - `task_id`, `channel`, `type` (S), `value` (B) = serialized write value
  - `expires_at` (N) = TTL as above

Methods to implement (sync; langgraph will call sync in this runtime):
- `put(config, checkpoint, metadata, new_versions) -> RunnableConfig` — put checkpoint item; return config with `configurable.checkpoint_id` set.
- `put_writes(config, writes, task_id, task_path="") -> None` — batch put write items.
- `get_tuple(config) -> CheckpointTuple|None` — if `checkpoint_id` in config, get that; else query latest by `sk` begins_with `checkpoint::` descending; load its writes; return `CheckpointTuple(config, checkpoint, metadata, parent_config, pending_writes)`.
- `list(config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]` — query checkpoint items for the thread, newest first, honoring `before`/`limit`.
- Serialize/deserialize strictly via `self.serde.dumps_typed` / `self.serde.loads_typed` (constructor can accept serde; default to the base's serde). Store `(type, bytes)` and reconstruct on read.
- `get_next_version(current, channel)` — can delegate to base or implement integer bump.

Document: the checkpointer table MUST have TTL enabled on `expires_at`.

## Agent flow (`agent.py`)

State (TypedDict): `messages: Annotated[list, add_messages]`, `authorized: bool`, `user_id: str|None`, `conversation_id: str`, `whatsapp_number: str`.

Graph nodes:
1. `agent` node — system prompt (welcome + ask for ID to authorize; after auth, collect `user_name, user_email, user_address, products, number_of_products`; then confirm before ordering). Calls `llm.bind_tools([get_current_time, create_order])`.
2. Tools: `get_current_time()` → UTC ISO + human-readable; `create_order(user_name, user_email, user_address, products, number_of_products, conversation_id)` → POST `ORDER_API_ENDPOINT` (JSON body incl. `conversation_id`, optional `Authorization: Bearer {ORDER_API_KEY}`, `requests` with timeout), parse `workflow_id`, on success `put_mapping(conversation_id, workflow_id, user_id, whatsapp_number)`, return dict incl. workflow_id. `user_id`/`whatsapp_number` reach the tool via state (closure/injected config), not LLM args.
3. `confirm` interrupt — before executing `create_order`, call `interrupt({...collected params...})`; resume value = user's reply; proceed only on explicit yes.
4. Authorization gate — reject + re-ask until `is_authorized_user` passes.

`run_agent(conversation_id, user_text, whatsapp_number="") -> str`: build graph with `DynamoDBSaver`, `config={"configurable": {"thread_id": conversation_id}}`; if pending interrupt → `invoke(Command(resume=user_text), config)` else `invoke({"messages": [HumanMessage(user_text)], "conversation_id": ..., "whatsapp_number": ...}, config)`; return last AI text (interrupt prompt text when paused).

## FastAPI (`app.py`)

- `app = FastAPI()`, permissive CORS (`allow_origins=["*"]`, methods, headers) so `file://` HTML can call it.
- `GET /` → serve `test_page.html` (FileResponse / HTMLResponse).
- `POST /ask` body `{conversation_id, message}` → `run_agent(conversation_id, message)`; append user + assistant turns to chat history; return `{reply, conversation_id}`.
- `POST /callback` body `{conversation_id, receipt_id, total_cost}` → `get_mapping(conversation_id)` for `whatsapp_number`; compose `"Your order is complete. Receipt {receipt_id}. Total cost {total_cost}."`; send via WhatsApp helper (socialmessaging pattern) guarded by env — if `WHATSAPP_PHONE_NUMBER_ID` absent or number blank, log and skip send; always `append_turn` assistant message; return `{status: "ok"}`.
- `handler = Mangum(app)`.
- `lambda_handler(event, context)`: dispatcher (SQS → `process_sqs_batch`; else `handler(event, context)`).
- `process_sqs_batch(event)`: reuse `extract_webhook_data`/`extract_text_messages`; per message `conversation_id = sender`, `run_agent(sender, text, whatsapp_number=sender)`, `send_whatsapp_message(sender, reply)`, append turns; return `{"batchItemFailures": [...]}` with per-record isolation like `handler.py`.

## HTML test page (`test_page.html`)

Vanilla JS + fetch, no build. Inputs: Function URL base, conversation_id (default random), message box, Send → `POST {base}/ask`, render chat bubbles (including interrupt/confirm prompt so user can type "yes"). Callback section: `receipt_id` + `total_cost` inputs + button → `POST {base}/callback` with current conversation_id.

## requirements.txt / README

requirements.txt = the pinned list above. README additions: which packages go in a layer (everything except boto3), build steps (`pip install -r requirements.txt -t python/ ; zip -r layer.zip python`), env vars, handler path `app.lambda_handler`, Function URL must be enabled/public, checkpointer table needs TTL on `expires_at`, the four tables + keys.

## Verification approach

Project has no test framework yet → add `pytest` + `moto` (or hand-rolled boto3 stubs) and `pytest.ini`. All tests run offline with Bedrock + DynamoDB mocked.

- Import check: `python -c "import app, agent, checkpointer, data, whatsapp"` in a venv with requirements installed.
- `run_agent` mocked smoke tests: unauthorized-reject, authorized-proceed, interrupt-before-HTTP (no POST fired), resume-triggers-POST + mapping-write, callback-produces-message + history-write.
- FastAPI `TestClient`: `GET /` returns HTML 200; `POST /ask` returns `{reply, conversation_id}` with Bedrock/DynamoDB mocked.

---

# Ordered items

- [ ] 1. Create `data.py`: env-driven table name reads, cached `boto3.resource("dynamodb")` handles, and the four-table access functions (`is_authorized_user`, `append_turn`, `put_mapping`, `get_mapping`).
      Files: `data.py`
      Verify: `python -m pytest tests/test_data.py` (moto-mocked DynamoDB) passes.

- [ ] 2. Create `checkpointer.py`: `DynamoDBSaver(BaseCheckpointSaver)` with `put`, `put_writes`, `get_tuple`, `list`, serde via `self.serde`, item shapes + `expires_at` TTL as designed.
      Files: `checkpointer.py`
      Verify: `python -m pytest tests/test_checkpointer.py` — round-trip put→get_tuple restores a checkpoint; writes persisted/loaded; TTL attribute set.

- [ ] 3. Create `whatsapp.py`: thin re-export wrapper importing `send_whatsapp_message`, `extract_webhook_data`, `extract_text_messages`, `normalize_destination` from `whatsapp_lambda`.
      Files: `whatsapp.py`
      Verify: `python -c "import whatsapp; whatsapp.send_whatsapp_message"` resolves (with boto3 importable).

- [ ] 4. Create `agent.py`: state, `get_current_time` + `create_order` tools, `ChatBedrockConverse` bound to tools, graph with auth gate + `interrupt` confirm before `create_order`, and `run_agent(conversation_id, user_text, whatsapp_number="")`.
      Files: `agent.py` (depends on 1, 2)
      Verify: `python -m pytest tests/test_agent.py` — the five mocked `run_agent` scenarios pass (unauthorized-reject, authorized-proceed, interrupt-before-HTTP, resume-triggers-POST+mapping-write, callback path tested in item 5).

- [ ] 5. Create `app.py`: FastAPI app, CORS, `GET /`, `POST /ask`, `POST /callback`, `Mangum` handler, and the `lambda_handler` dispatcher + `process_sqs_batch`.
      Files: `app.py` (depends on 1, 3, 4)
      Verify: `python -m pytest tests/test_app.py` — TestClient `GET /`=200 HTML, `POST /ask` shape, `POST /callback` composes message + writes history (Bedrock/DynamoDB/WhatsApp mocked); dispatcher routes an SQS event to `process_sqs_batch`.

- [ ] 6. Create `test_page.html`: vanilla JS chat UI + callback simulator posting to `{base}/ask` and `{base}/callback`.
      Files: `test_page.html`
      Verify: open in browser / `python -c "import pathlib,html.parser as h; ..."` lint; manual check that fetch URLs build from base input. (No build step.)

- [ ] 7. Update `requirements.txt` (pinned list) and `README.md` (layer packaging, env vars, handler path, Function URL public, checkpointer TTL, four tables + keys).
      Files: `requirements.txt`, `README.md`
      Verify: `pip install -r requirements.txt` resolves in a clean Python 3.12 venv; `python -c "import app, agent, checkpointer, data, whatsapp"` succeeds.

- [ ] 8. Add test harness: `pytest.ini`/`conftest.py` with env var fixtures and Bedrock/DynamoDB mocks; ensure `python -m pytest` runs the full suite green.
      Files: `conftest.py`, `pytest.ini`, `tests/*`
      Verify: `python -m pytest -q` — all tests pass offline.
