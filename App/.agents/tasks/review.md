# Review — swap create_order → list_orders, remove auth, reshape /callback

Commit under review: `5459b41` (feat: replace create_order with read-only list_orders, drop user auth, reshape /callback). Path note: the real tree is `App\`, not `src\src\` — the plan records this correction and the review was performed against `App\`.

## Verification evidence (read, not rerun)
The commit message records: `pytest -q -> 31 passed, exit 0`; import check for app/agent/data/whatsapp/checkpointer/logging_setup ok; TestClient `/callback` parallel-list body -> 200 + composed multi-line message and missing optional field -> 200 (not 422); grep showing `USERS_TABLE_NAME` only inside dormant `data._users_table`; `function.zip` rebuilt with the seven modules. Required verification is present, so the suite was not rerun. The 31 (vs. the plan's 32 baseline) reflects the intended test consolidation — a manual count of the current suite is exactly 31 (test_agent 7, test_app 11, test_data 8, test_checkpointer 4, test_integration 1), consistent with the record.

## Criteria check
1. **list_orders / POST body / ids** — PASS. `list_orders_request` POSTs `{"conversation_id": conversation_id, "customer_id": customer_id}` — exactly those two keys. `tools_node` sources `conversation_id` from `state["conversation_id"]` (model-supplied arg ignored). Workflow id parsed from response `id` field, coerced via `str(raw_id)`. User-facing message states the lookup started with the workflow id.
2. **HITL interrupt** — PASS. `confirm_node` keeps `interrupt(...)` with prompt "Do you want to list the orders for that customer id? (customer id: <id>) (enter: yes or no)", same yes/no/ambiguous-reask logic. No POST before confirm (`route_after_agent` → confirm → tools).
3. **Auth fully removed** — PASS. No `verify_user`, no auth gate in `tools_node`, no `not_authorized` branch; agent.py references none of `is_authorized_user`/`get_user`/profile prefill (grep: no matches). `USERS_TABLE_NAME` appears only in dormant `data._users_table()`, unreachable from the request path, so the app does not crash when it is unset. Dormant functions remain in data.py as allowed.
4. **Mapping row shape** — PASS. `put_mapping` stores `{conversation_id, workflow_id, customer_id, whatsapp_number, channel}`; `channel = "whatsapp" if wa_number else "api"` derived in `tools_node`.
5. **/callback reshape** — PASS. Matched on `conversation_id` alone (404 if no mapping); uses stored `workflow_id` (not received); `CallbackRequest` fields optional with defaults (missing field → no 422, test confirms); `_as_list` tolerates list OR scalar; "No orders found…" when not found or all empty, else zipped multi-line list to max length with "" fill; channel routing intact; assistant message always appended; returns `{status, channel}`; both exception handlers retained.
6. **Unchanged behaviors** — PASS. DynamoDB checkpointer + interrupt/resume (integration test), logging_setup + `@log_io` on new functions with debug logs, dual SQS/HTTP dispatch, unmasked history writes, `/history` + Load history, `.strip()` table-name hardening all preserved.
7. **test_page.html** — PASS. `customerFound` checkbox + five comma-separated inputs split into parallel arrays via `splitList`; `conv` field, Send callback, Load history buttons, two-column layout, and element IDs (conv/base/chat/message/send/load/callback) intact; `receipt`/`total` removed.
8. **Tests + zip** — PASS. Tests rewritten per plan and recorded green (31 passed); `function.zip` verified to contain exactly app.py, agent.py, checkpointer.py, data.py, whatsapp.py, logging_setup.py, test_page.html.

## Note (non-blocking)
The acceptance text quotes the no-orders message as "No orders found for the requested customer." while the implementation appends "(workflow <id>)". This is the approved trade-off from the original user request (message 5: the callback message should mention the workflow id) and is codified in the plan and asserted by tests. Not a blocker.

## Verdict
No blocking findings. APPROVED.
