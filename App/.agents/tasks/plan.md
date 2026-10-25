# Implementation Plan — swap create_order → list_orders, remove authorization, reshape callback + test UI

## Path correction (read first)

The task brief referenced paths under `...\src\src\`, but that directory does not
exist in this repo. The real layout, confirmed by exploration, is:

- **Repo base / test cwd:** `d:\GenAI Projects\AWS Hackathon\AWS CDS Agentic AI Partner Hackathon\App`
- **Python source modules:** `App\HackathonWhatsAppCode\` — `agent.py`, `app.py`, `data.py`, `test_page.html`, `logging_setup.py`, `whatsapp.py`, `checkpointer.py`
- **Tests:** `App\tests\` (`test_agent.py`, `test_app.py`, `test_data.py`, `test_integration.py`, `test_checkpointer.py`); shared fixtures in `App\conftest.py`
- **Docs:** `App\README.md`
- **Test venv:** `App\.venv_test` (persistent; dependencies already installed)
- There is **no** `src\old` archive directory in this workspace, so the "do not touch old" constraint has no target here. The README mentions `../old/` narratively; leave that prose as-is unless a specific edit below touches it.

**Test command** (run from `App\`, pythonpath is preconfigured in `pytest.ini` as `HackathonWhatsAppCode .`):

```powershell
& .\.venv_test\Scripts\python.exe -m pytest -q
```

Baseline before any change: **32 tests pass** (verified).

## Assumptions resolved (and why)

1. **Field name for the user-supplied id.** The brief says the tool input is `customer_id`. The RPA POST body is exactly `{ "conversation_id", "customer_id" }`. I use `customer_id` throughout the new tool and mapping row, replacing the old `user_id`.
2. **Mapping row shape.** Brief item 3 says `{ conversation_id (PK), workflow_id, customer_id, whatsapp_number, channel }`. `data.put_mapping` currently takes positional `(conversation_id, workflow_id, user_id, whatsapp_number, channel)`. I rename the `user_id` parameter to `customer_id` (positional order unchanged) so every caller stays positional-compatible and the stored attribute becomes `customer_id`.
3. **CallbackRequest leniency.** Brief item 4 requires missing fields to NOT 422. I make every per-order list field `Optional` with a default of `[]`, `customer_found` default `False`, and accept either a list or a scalar for each column (scalars are wrapped into a single-element list during composition). `conversation_id` stays required (matching is impossible without it).
4. **`USERS_TABLE_NAME` must not be required.** `data.py` reads it lazily only inside `is_authorized_user`/`get_user` via `_users_table()`. Since the agent path will no longer call those, nothing in the request path touches `USERS_TABLE_NAME`. I leave the functions dormant (per brief), so no code change is needed to "not crash if unset" — but I remove the `USERS_TABLE_NAME` requirement from README and confirm no request-path call remains.
5. **conftest keeps the users table.** The brief allows keeping it (harmless). I keep it so `test_data.py`'s dormant-function tests still pass, but I make the agent/integration tests seed nothing in it.
6. **Confirm prompt uses the customer id.** The new confirm interrupt message is `"Do you want to list the orders for that customer id? (customer id: <customer_id>) (enter: yes or no)"`, reusing the existing yes/no/ambiguous `confirm_node` machinery unchanged.
7. **Deliverable location.** Plan written to `App\.agents\tasks\plan.md` (the real equivalent of the requested path).

## Decomposition decision

This change is a single cohesive unit: the new `list_orders` contract, the mapping-row rename, the callback reshape, the UI, the tests, and the README all depend on one another and on the same new data shape. It does **not** split into independently shippable features. Therefore this is a single ordered plan for the existing implement-and-review loop — the workflow tail is **not** restructured.

---

# Implementation Plan

- [ ] 1. Rename the mapping attribute from `user_id` to `customer_id` in `data.put_mapping`.
      Change the parameter name `user_id` → `customer_id` and the stored item key `"user_id"` → `"customer_id"` in `put_mapping`. Keep the positional signature order `(conversation_id, workflow_id, customer_id, whatsapp_number, channel)` and the `@log_io()` decorator. Leave `is_authorized_user`, `get_user`, `_users_table`, and `USERS_TABLE_NAME` usage in `data.py` untouched (dormant).
      Files: `App\HackathonWhatsAppCode\data.py`
      Verify: `& .\.venv_test\Scripts\python.exe -m pytest tests/test_data.py -q` — expect the mapping round-trip test to FAIL here (it still asserts `user_id`); it is updated in step 7. Confirm no other test in test_data.py breaks (the users/history tests still pass).

- [ ] 2. Replace the `create_order`/`verify_user` tooling in `agent.py` with a read-only `list_orders` tool and remove the authorization machinery.
      Do all of the following in `agent.py`:
      - Rewrite `SYSTEM_PROMPT`: greet, ask the user for their **customer id** (no authorization/user-id verification wording, no profile-prefill wording), confirm, then call `list_orders`. Keep the `get_current_time` sentence.
      - Remove the `verify_user` tool and `_verify_user_lookup` helper. Remove `create_order`. Add a new `@tool list_orders(customer_id: str, conversation_id: str) -> dict` whose docstring says conversation_id is injected from state. It delegates to a renamed `@log_io() list_orders_request(*, customer_id, conversation_id, whatsapp_number, channel)` built by **copying `place_order` verbatim** for the endpoint/`ORDER_API_KEY` bearer/`ORDER_API_TIMEOUT`/headers/`raise_for_status`/`id`-parsing logic, but with POST body exactly `{"conversation_id": conversation_id, "customer_id": customer_id}` and a user-facing message like `"I've started the lookup for customer <customer_id>. Your workflow ID is <workflow_id>. I'll share the orders here once they arrive."`. Call `data.put_mapping(conversation_id, workflow_id, customer_id, whatsapp_number, channel)`.
      - `AgentState`: drop `authorized`, `user_id`, `profile_name`, `profile_email`, `profile_address`. Keep `messages`, `conversation_id`, `whatsapp_number`, `reconfirm`.
      - `confirm_node`: replace `_format_order_summary` with a summary that shows the customer id and the prompt `"Do you want to list the orders for that customer id? (customer id: <customer_id>) (enter: yes or no)"`. Keep `interrupt(...)`, the `reconfirm` re-ask branch, and the yes/no/ambiguous logic exactly. Pull the pending customer_id from the `list_orders` tool call args.
      - Rename `_pending_create_order_call` → `_pending_list_orders_call` (match `"list_orders"`).
      - `tools_node`: remove the authorization gate and the `not_authorized` branch and the `verify_user` handling. For a `list_orders` call, source `conversation_id` from `state["conversation_id"]`, `whatsapp_number` from state, derive `channel = "whatsapp" if wa_number else "api"`, and call `list_orders_request(...)`. All other tools (`get_current_time`) run via `TOOLS_BY_NAME[name].invoke(args)`. Drop the `authorized`/`user_id`/`profile_*` state updates.
      - `TOOLS = [get_current_time, list_orders]`.
      - `route_after_agent`: route to `"confirm"` when any tool call is `list_orders`, else `"tools"`.
      - `run_agent` seed state: drop `authorized`/`user_id`/`profile_*` keys from the new-thread payload; keep `messages`, `conversation_id`, `whatsapp_number`, `reconfirm`.
      - Keep `@log_io()` on `agent_node`, `confirm_node`, `tools_node`, `run_agent`, `list_orders_request`, and add debug input/output logs in `list_orders_request` matching the existing `place_order` style.
      Files: `App\HackathonWhatsAppCode\agent.py`
      Verify: `& .\.venv_test\Scripts\python.exe -c "import sys; sys.path.insert(0,'HackathonWhatsAppCode'); import agent; print([t.name for t in agent.TOOLS])"` from `App\` prints `['get_current_time', 'list_orders']`. test_agent.py will fail until step 6 — that is expected.

- [ ] 3. Reshape `CallbackRequest` and the `/callback` message composition in `app.py`.
      - Replace `CallbackRequest` with: `conversation_id: str` (required); `customer_found: bool = False`; and `Doc_dates`, `Sales_docs`, `Net_Prices`, `Currencies`, `Cust_Name` each typed to accept a list or scalar with default `[]` (e.g. `Optional[object] = []` or `list[str] | str = []`), so a missing field does not 422.
      - In `callback`, after the existing mapping lookup + 404 (unchanged, matched on `conversation_id` alone, workflow_id read from the stored mapping): add a helper `_compose_order_message(workflow_id, body)` that normalizes each field to a list (wrap a scalar/str in a one-element list, treat `None`/`""` as `[]`), and:
        * if `not customer_found` OR every list is empty → `"No orders found for the requested customer (workflow <workflow_id>)."`
        * else build one line per order by iterating `range(max_len)` across the five lists, using `""` for missing cells, formatted as `"<n>) Sales doc <Sales_docs[i]> | <Net_Prices[i]> <Currencies[i]> | customer <Cust_Name[i]> | date <Doc_dates[i]>"`, prefixed with `"Here are the orders for workflow <workflow_id>:"`.
      - Keep channel routing (whatsapp gated on `WHATSAPP_PHONE_NUMBER_ID` + mapping `whatsapp_number`; api stores only), always `append_turn` the composed message, return `{"status": "ok", "channel": channel}`. Update the `callback` debug input log to reference the new fields.
      - Leave both exception handlers (`RequestValidationError`, generic `Exception`) untouched. Keep `@log_io()` on the route.
      Files: `App\HackathonWhatsAppCode\app.py`
      Verify: `& .\.venv_test\Scripts\python.exe -c "import sys; sys.path.insert(0,'HackathonWhatsAppCode'); import app; print(app.CallbackRequest.model_fields.keys())"` from `App\` lists the new fields. test_app.py callback tests fail until step 8 — expected.

- [ ] 4. Update `test_page.html` callback simulator to the new result shape.
      In the "Callback simulator" section, remove the `receipt`/`total` inputs. Add a `customer_found` checkbox (`id="customerFound"`) and five text inputs (`id="docDates"`, `id="salesDocs"`, `id="netPrices"`, `id="currencies"`, `id="custName"`) each taking comma-separated values, with a short hint that values are comma-separated and zipped by index. Keep the two-column 60/40 layout, the existing `conv` input, the `#callback` "Send callback" button, and the "Load history" button/JS intact. Rewrite `sendCallback()` to read the checkbox and split each text input on commas (trim, drop empties) into arrays and POST `{ conversation_id, customer_found, Doc_dates, Sales_docs, Net_Prices, Currencies, Cust_Name }`. Keep all other element IDs the JS depends on (`conv`, `base`, `chat`, `message`, `send`, `load`) unchanged. Update the right-column hint that currently says "Type your user ID first to get authorized" to say to provide a customer id and reply `yes` at the confirm prompt.
      Files: `App\HackathonWhatsAppCode\test_page.html`
      Verify: `& .\.venv_test\Scripts\python.exe -c "p=open('HackathonWhatsAppCode/test_page.html',encoding='utf-8').read(); assert 'customerFound' in p and 'receipt' not in p and 'Sales_docs' in p"` from `App\` exits 0 (structural check; functional UI is validated manually in a browser).

- [ ] 5. Update `conftest.py` so nothing in the request path requires the users table, while keeping the four-table fixture usable.
      Keep the `USERS_TABLE_NAME` constant, the `os.environ.setdefault(...)`, the autouse `_env` monkeypatch set, and the users table creation in the `dynamodb` fixture (all harmless and still used by `test_data.py`). No functional change is strictly required here; if the `order_response`/`mock_requests_post`/`scripted_llm` fixtures are reused by the rewritten tests, leave them as-is. Make no change that removes the other three tables.
      Files: `App\conftest.py`
      Verify: covered by the full suite in step 9 (conftest has no standalone test).

- [ ] 6. Rewrite `tests/test_agent.py` for the list_orders flow.
      Replace all verify_user/authorization/profile-prefill scenarios. New coverage, using the existing `FakeLLM`/`mocked` harness pattern (drop `get_user`/`is_authorized`/`set_user`; keep `put_mapping` and `post` mocks and the InMemorySaver graph rebuild):
      - `_ai_list_orders(customer_id, call_id)` helper emitting a `list_orders` tool call with args `{"customer_id": ..., "conversation_id": "ignored"}`.
      - (a) agent asks for customer id then emits list_orders → the confirm interrupt fires with the new prompt (assert `"list the orders"` and the customer id appear, `"yes or no"` present) and **no** `requests.post` before confirm.
      - (b) resume `"yes"` → `requests.post` called once; assert `kwargs["json"] == {"conversation_id": <conv>, "customer_id": <id>}` exactly (two keys, state conversation_id); `put_mapping` called once with `(conv, "237436573", customer_id, whatsapp_number, "whatsapp")`; and a separate api-channel test asserting `""`/`"api"` when no whatsapp_number.
      - (c) resume `"no"` → no post, no mapping.
      - (d) ambiguous reply → still paused, re-asks, no post.
      - Keep an `ORDER_API_KEY` bearer-header test and the RPA-`id`-field→`"237436573"` parsing assertion (via the mocked response already shaped `{"key":...,"state":"Pending","id":237436573}`).
      Files: `App\tests\test_agent.py`
      Verify: `& .\.venv_test\Scripts\python.exe -m pytest tests/test_agent.py -q` — all tests pass.

- [ ] 7. Update `tests/test_data.py` mapping tests to the new row shape.
      In `test_put_and_get_mapping_round_trip`, call `put_mapping(..., customer_id="...", ...)` and assert the returned/stored item has `customer_id` instead of `user_id`. Keep `test_is_authorized_user_*` (functions remain dormant but present), `append_turn`, history, and `get_mapping_missing` tests unchanged.
      Files: `App\tests\test_data.py`
      Verify: `& .\.venv_test\Scripts\python.exe -m pytest tests/test_data.py -q` — all tests pass (closes the expected failure from step 1).

- [ ] 8. Reshape `tests/test_app.py` callback tests to the new CallbackRequest.
      Replace the four callback tests. New coverage (keep the `mocked`/`client` fixtures, dispatcher routing tests, `/history` test, and index/ask tests unchanged):
      - `customer_found=True` with parallel lists (e.g. two orders) + `channel="api"` mapping → assert the composed multi-line message (contains `"Here are the orders for workflow 237436573"` and one line per order with the zipped values), `append_turn` called once with it, `send_whatsapp_message` not called.
      - `customer_found=True` + `channel="whatsapp"` mapping with `WHATSAPP_PHONE_NUMBER_ID` set + `whatsapp_number` → `send_whatsapp_message` called once with the composed text.
      - `customer_found=False` → message asserts the "No orders found" text (with workflow id), history written.
      - unknown conversation (`get_mapping` returns None) → 404, no append/send.
      - lenient: POST `{"conversation_id":"c"}` with ALL per-order fields omitted → status 200 (NOT 422), message is the "No orders found" branch. (Replaces the old missing-field-422 test.)
      Files: `App\tests\test_app.py`
      Verify: `& .\.venv_test\Scripts\python.exe -m pytest tests/test_app.py -q` — all tests pass.

- [ ] 9. Update `tests/test_integration.py` to the list_orders end-to-end flow.
      Seed nothing in the users table. Script the LLM to: ask for the customer id, then emit a `list_orders` tool call (args include a `customer_id`); turn 2 reaches the confirm interrupt (no POST, no mapping yet, `state.next` truthy via the real `DynamoDBSaver`); resume `"yes"` → `requests.post` fired once with `json == {"conversation_id": <conv>, "customer_id": <id>}`, and `data.get_mapping(conv)` returns a row with `customer_id`, `workflow_id == "237436573"`, `whatsapp_number`, `channel == "whatsapp"`. Remove the `_ai_verify`/users-table-seeding bits and the `USERS_TABLE_NAME` import if unused.
      Files: `App\tests\test_integration.py`
      Verify: `& .\.venv_test\Scripts\python.exe -m pytest tests/test_integration.py -q` — the end-to-end test passes.

- [ ] 10. Run the full offline suite and confirm green.
      Files: none (verification only)
      Verify: from `App\`, `& .\.venv_test\Scripts\python.exe -m pytest -q` — all tests pass with no failures or errors.

- [ ] 11. Update `README.md` to the new behavior.
      - Replace `create_order` narrative/module-table entry with `list_orders` (input `customer_id`; async POST body `{conversation_id, customer_id}`; parses `id`; stores mapping; async callback delivers the order list). Remove the authorization/`verify_user`/profile-prefill-in-agent paragraphs and the confirm-step wording about placing an order (change to listing orders).
      - Remove the `USERS_TABLE_NAME` row from the env-var table and the entire "Users table" schema + "Seeding dummy data" sections (note the users table is deleted/unused); keep the other three tables and their seeding notes. Change "Four tables" wording to three.
      - Update the mapping-table schema: `customer_id` replaces `user_id`.
      - Update the `POST /callback` row and the "Callback matching"/"Channel routing"/"Both triggers persist" notes to the new body `{ conversation_id, customer_found, Doc_dates, Sales_docs, Net_Prices, Currencies, Cust_Name }` and the order-list message format, referencing `list_orders` instead of `create_order`.
      - Update the SQS example `text.body` and prose that say "my id is U1001" to a customer-id phrasing.
      Files: `App\README.md`
      Verify: `& .\.venv_test\Scripts\python.exe -c "r=open('README.md',encoding='utf-8').read(); assert 'list_orders' in r and 'USERS_TABLE_NAME' not in r and 'customer_id' in r"` from `App\` exits 0 (doc consistency check).

- [ ] 12. Rebuild the deployment function zip.
      From `App\HackathonWhatsAppCode\` (the directory holding the modules), run the PowerShell packaging. No new module was introduced, so the file list is unchanged.
      ```powershell
      $ProgressPreference='SilentlyContinue'
      if (Test-Path function.zip) { Remove-Item -Force function.zip }
      Compress-Archive -Path app.py,agent.py,checkpointer.py,data.py,whatsapp.py,logging_setup.py,test_page.html -DestinationPath function.zip -CompressionLevel Optimal
      ```
      Files: produces `App\HackathonWhatsAppCode\function.zip`
      Verify: `Test-Path .\function.zip` from `App\HackathonWhatsAppCode\` returns `True`; the archive lists the seven expected files.

## Notes for the implementer

- `data.py`'s `is_authorized_user`/`get_user`/`_users_table` stay in place (dormant) — do not delete them, do not call them from `agent.py`.
- `whatsapp.py`, `checkpointer.py`, `logging_setup.py` are unchanged.
- Every new function (`list_orders_request`, `_compose_order_message`) carries `@log_io()` where appropriate plus explicit DEBUG input/output logs matching the `place_order` precedent.
- The POST body for the RPA call must contain **exactly** the two keys `conversation_id` and `customer_id` — tests assert equality, not membership.
- Run tests after each code step; steps 1 and 2 intentionally leave specific tests red until their matching test-update step lands (noted inline).
