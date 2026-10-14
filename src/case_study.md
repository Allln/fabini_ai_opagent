# Case study: AI Operations Agent

## What was decided and why

- **Python + FastAPI:** small typed service boundary with a browser UI.
- **Gemini + native function calling:** demonstrates real model-selected tools.
- **Deterministic mock adapter:** makes the same contract reproducible without
  credentials or network and keeps tests stable.
- **SQLite audit storage:** enough persistence for a local POC without adding
  infrastructure.
- **Approval for `retry_sync` and `create_ticket`:** both can change external
  operational state, unlike the four read-only `get_*` tools.
- **Explicit action intent:** the deterministic walkthrough only requests an
  action when the user explicitly asks for it; the host enforces the same
  policy for Gemini proposals. Negated requests do not trigger approval.
  Approval is still required before execution.
- **Bounded orchestration:** allow-listed schemas, per-tool timeout, one retry
  only for read-only tools, iteration limit, and action idempotency prevent
  uncontrolled execution.

The implementation is a POC with simulated in-process tools, not a production
integration. The audit trail records structured events and tool metadata, not
hidden model reasoning.

## Common flow

The browser calls `POST /api/runs`. The selected adapter returns text or a
function call. The orchestrator validates and executes allowed tools, sends
results back to the adapter, and returns a structured result containing status,
findings, steps, tool calls, approvals, errors, timestamps, and run ID.

## Scenario 1 — successful diagnosis, no side effect

**Request:** `Find out why order 100245 was not shipped and suggest next steps.`

### Mock mode — automated and reproducible

Calls, in order:

1. `get_order` → paid order in warehouse `CZ-01`
2. `get_payment` → payment `captured`
3. `get_fulfillment` → `blocked`, reason `sync_error`
4. `get_sync_logs` → latest event `Warehouse API timeout`
5. `get_order_audit` → related local audit records

Result: `completed`, with findings identifying the blocked fulfillment and
synchronization timeout. No side effect is called. Covered by
`test_successful_diagnosis_uses_multiple_read_only_tools`.

### Gemini mode — manually observed

Gemini made the same four read-only calls and returned a Czech diagnosis. It
recommended retrying synchronization and creating a support ticket, but
returned neither function call, so no approval was requested. This is an
observed run, not a guaranteed sequence for every Gemini response.

## Scenario 2 — approved synchronization retry

**Request:** `Find out why order 100245 was not shipped and retry the sync.`

### Mock mode — automated and reproducible

After the same read-only calls and local audit lookup, the adapter proposes
`retry_sync({"order_id": "100245"})`. The run becomes `waiting_approval`.
Approval executes the simulated action once, returns `queued`, and finishes
`completed`; rejection finishes `blocked` without executing it. Duplicate
approval is idempotent. Covered by the agent and API tests.

### Gemini mode — manually observed

Gemini made the four read-only calls, returned `retry_sync`, paused at the
host-side approval gate, and after approval recorded a successful `retry_sync`
call. The mock action only returns a simulated result; no warehouse is called.

## Scenario 3 — tool failure

**Request:** `Find out why order 999999 was not shipped.`

### Mock mode — automated and reproducible

`get_order(999999)` returns `Order not found for order 999999`. The error is
recorded, no side effect is attempted, and the adapter returns a safe response
stating that the investigation could not be completed. The API status is
`failed` because a tool error was recorded; it is not a successful diagnosis.
Covered by
`test_tool_failure_is_recorded_and_returns_safe_result`.

### Gemini mode — manually observed

`get_order`, `get_fulfillment`, and `get_sync_logs` all failed for the missing
order. Gemini called no side-effecting tool and returned a Czech message that
the order was not found. The run is `failed` with failed tool records and
no separate findings. Continuing with additional read-only calls is
model-dependent; the iteration limit and audit trail bound and expose it.

## What is verified

- Mock multi-step diagnosis, approval/rejection, negated intent, batched tool
  calls, invalid action arguments, host-side action-intent enforcement, failure
  handling, iteration limit, duplicate approval protection, Gemini adapter
  translation, API endpoints, and frontend serving: 21 automated tests
  pass.
- Ruff lint and formatting checks pass.
- The three Gemini outcomes above were manually observed with a configured API
  key; they are examples, not deterministic guarantees.

Mypy passes for the application package.

## POC limitations

There is no authentication, authorization, real external integration,
multi-tenant isolation, background worker, or durable resumption of a pending
model conversation after process loss. A production version would add those
controls, authenticated integration clients, durable approval state, live
Gemini contract tests, and integration-level idempotency.
