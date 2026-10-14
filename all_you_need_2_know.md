# FABINI AI Operations Agent - all you need 2 know
## The goal
We want to demonstrate:

- a natural-language request becoming a structured investigation;
- typed tools collecting evidence from several operational areas;
- a clear difference between a reproducible mock agent and a real
  model-selected tool-calling flow;
- a human approval step before side effects;
- a useful result with findings, tool calls, approvals, errors, and a timeline;
- local SQLite storage so the run can be inspected afterwards.

This is deliberately a focused operations agent, not a general chatbot. It is
better at showing one controlled job clearly than claiming to solve every
operations problem.

## How the POC works

The short version:

1. The user submits an order question in the browser.
2. FastAPI sends it to the orchestrator.
3. The selected adapter (mock or Gemini) decides what to do next.
4. The orchestrator validates the tool name and arguments, applies limits, and
   runs an allowed tool.
5. Tool results go back to the adapter so the investigation can continue.
6. A diagnosis is returned, or an action pauses at the approval gate.
7. The final result and the audit trail are stored in SQLite.

The browser also draws a small order-state graph from the returned evidence. It
is only a readable view of the result; it is not another agent or database

The two flows are shown here:

- [Mock: fixed investigation sequence](src/poc-flow-deterministic.svg)
- [Gemini: model-selected tool loop](src/poc-flow-gemini.svg)

## Mock mode vs Gemini mode

| | Deterministic mock | Gemini |
|---|---|---|
| Why it exists | Reliable demo path and stable tests | Show real native function calling |
| Who chooses the next tool | A scripted walkthrough | Gemini, using the tool schemas and returned evidence |
| Repeatability | Same request follows the same expected sequence | The sequence can vary by response |
| Network / key | No network and no API key | Requires `GEMINI_API_KEY` and network access |
| Host controls | Validation, limits, approval, and audit | The same controls still apply |

The model does not directly access SQLite or an external system. It proposes a
tool call; the host application decides whether that call is valid and whether
it is allowed to run.

## Architecture in plain language

- **Browser UI**: small vanilla HTML, CSS, and JavaScript interface.
- **FastAPI**: serves the UI and exposes the run and approval endpoints.
- **Agent orchestrator**: owns the bounded loop, policy checks, approval flow,
  idempotency, and final result.
- **Adapters**: one deterministic mock adapter and one Gemini adapter behind the
  same interface.
- **Tool registry**: allow-listed tools with Pydantic argument validation,
  timeouts, read-only retry behavior, and simulated handlers.
- **SQLite repository**: stores runs, events, tool calls, and approvals.

There are seven tools:

- Read-only: `get_order`, `get_payment`, `get_fulfillment`,
  `get_sync_logs`, `get_order_audit`
- Side-effecting: `retry_sync`, `create_ticket`

Read-only investigation is automatic. Side-effecting tools need both an
explicit request from the user and approval in the UI. Saying "do not retry"
does not count as a request.

## Technology

- Python 3.12
- FastAPI and Uvicorn
- Pydantic and pydantic-settings
- Google Gemini API, optional
- SQLite
- Vanilla HTML, CSS, and JavaScript
- pytest, Ruff, and mypy configuration for verification

## Run it locally

From the `src` directory:

```powershell
py -3.12 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
..\.venv\Scripts\python.exe -m app.main
```

Open <http://127.0.0.1:8000>.

Mock mode is the default. It works without a key and is the best place to start.

To try Gemini, set this in `.env`:

```dotenv
AGENT_MODE=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.1-flash-lite
```

Gemini responses are model-dependent. The mock path is the reproducible one.

## What to test in the UI

Start with these requests in mock mode:

### 1. Normal blocked-order diagnosis

```text
Find out why order 100245 was not shipped and suggest next steps.
```

Expected: read-only investigation, a diagnosis about blocked fulfillment and
the warehouse sync timeout, no approval prompt.

### 2. Approved synchronization retry

```text
Find out why order 100245 was not shipped and retry the sync.
```

Expected: the same investigation, then a `retry_sync` approval. Approve it to
get a simulated `queued` result. Reject it to finish without the action.

### 3. Create an incident ticket

```text
Investigate order 100245 and create an incident ticket.
```

Expected: a `create_ticket` approval. The ticket is simulated and gets an
in-process `INC-...` identifier.

### 4. Healthy orders

```text
Investigate order 100246.
Investigate order 100247.
```

Expected: shipped orders with successful synchronization evidence and no
approval.

### 5. Pending fulfillment

```text
Investigate order 100248.
```

Expected: a paid order that is still pending in picking. It should explain the
`awaiting_pick` state rather than inventing a sync retry.

### 6. Tool failure

```text
Find out why order 999999 was not shipped.
```

Expected: an explicit failed run with the missing-order error recorded. It
should not look like a successful diagnosis.

### 7. Input and intent checks

```text
Show me the current warehouse status.
Investigate order 100245, but do not retry synchronization.
```

The first request has no numeric order ID, so the run should ask for one before
calling tools. The second should investigate the order but must not create an
approval for `retry_sync`.

For further inspection, it is useful to turn on **Show related DB details after the run**
and inspect the summary, findings, tool calls, approval state, and timeline.

## Important boundaries

- This is not connected to a real warehouse, payment, order, or ticket system.
- `retry_sync` only returns a simulated queued response.
- `create_ticket` only creates an in-process simulated ticket.
- There is no authentication, authorization, multi-tenant isolation, or
  production-grade background processing.
- Pending approvals expire after 15 minutes.
- Read-only failures can retry once; side effects are never retried
  automatically.
- The audit trail stores structured events and tool metadata, not hidden model
  reasoning.

## Data available in the mock database

This is the data the UI can investigate out of the box. It is defined in the
in-process mock tool registry, so it is available even when `fabini.db` has not
been created yet.

| Order ID | Order details | Payment | Fulfillment | Latest sync evidence |
|---|---|---|---|---|
| `100245` | Paid, 2 items, warehouse `CZ-01` | Captured (`pay-100245`) | Blocked: `sync_error` | Failed: `Warehouse API timeout` |
| `100246` | Paid, 1 item, warehouse `CZ-02` | Captured (`pay-100246`) | Shipped, tracking `CZ123456789` | Success: `Shipment synced` |
| `100247` | Paid, 4 items, warehouse `CZ-03` | Captured (`pay-100247`) | Shipped, tracking `CZ987654321` | Success: `Shipment synced` |
| `100248` | Paid, 1 item, warehouse `CZ-01` | Captured (`pay-100248`) | Pending: `awaiting_pick` | Retryable: `Picking not started` |

Any other numeric order ID, such as `343434`, produces an explicit tool
failure. The mock data does not change when `retry_sync` is approved; it only
returns the simulated action result.

## What is in `fabini.db`

The SQLite file is an audit store, not the source of the mock order data. It is
created locally after the app starts and runs are made. It contains:

- `runs`: request, mode, status, full structured result, and timestamps;
- `events`: the ordered audit timeline;
- `tool_calls`: tool name, arguments, status, result, and errors;
- `approvals`: requested action, arguments, status, and resolution time.

`get_order_audit` reads related local tool calls and approvals for the order
being investigated. That means the audit section becomes more useful after
running a few scenarios, especially the `100245` approval flow.

To inspect it from `src`:

```powershell
..\.venv\Scripts\python.exe scripts\browse_db.py
```

The database is local and should not be committed.
