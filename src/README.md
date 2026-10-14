# FABINI AI Operations Agent

Proof of concept for an operations agent that investigates e-commerce
requests, calls typed tools, requires approval for side effects, and returns a
structured audit trail.

The project uses Python 3.12, FastAPI, Pydantic, SQLite, vanilla HTML/CSS/JS,
and Google's Gemini API. The operational systems are simulated in-process:
this is not a production integration. Mock mode is intentionally a scripted
expert walkthrough for the documented scenarios, not general model reasoning.

## Run locally

Run these commands from the `src` directory:

```powershell
py -3.12 -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
..\.venv\Scripts\python.exe -m app.main
```

Open <http://127.0.0.1:8000>. Mock mode is the default and needs no key or
network. For a runtime-only install, use
`python -m pip install -r requirements.txt`.

## Gemini mode

Set these values in `.env`:

```dotenv
AGENT_MODE=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.1-flash-lite
```

Gemini uses native function calling. The host application still validates tool
names and arguments, verifies that side effects were explicitly requested,
records every call, limits iterations, and gates side-effecting calls. A
missing key produces an explicit failure; it is never returned to the browser.
Gemini results are model-dependent; the observed manual runs are recorded in
[case_study.md](case_study.md).

## Architecture and decisions

- [app/main.py](app/main.py) exposes the FastAPI API and serves the browser UI.
- [app/agent/adapters.py](app/agent/adapters.py) provides Gemini and
  deterministic mock adapters behind one interface.
- [app/agent/policy.py](app/agent/policy.py) centralizes explicit action-intent
  detection shared by the adapters and host.
- [app/agent/orchestrator.py](app/agent/orchestrator.py) owns the bounded
  multi-step loop, approvals, idempotency, and structured outcomes.
- [app/tools/registry.py](app/tools/registry.py) validates and executes the
  seven tools:
  `get_order`, `get_payment`, `get_fulfillment`, `get_sync_logs`,
  `get_order_audit`, `retry_sync`, and `create_ticket`.
- [app/db/repository.py](app/db/repository.py) persists runs, events, tool
  calls, and approvals in SQLite.
- [app/config.py](app/config.py) centralizes runtime defaults and small policy
  constants, including the Gemini system instruction.

Read-only `get_*` tools run automatically. `retry_sync` and `create_ticket`
require an explicit user request and human approval because they have side
effects. The host enforces this policy for both adapters; the deterministic
mock additionally provides a reproducible expert walkthrough, while Gemini
demonstrates real model-selected tool calling. SQLite was chosen to keep the
POC auditable and local; the UI is framework-free to keep the demo small.

`get_order_audit(order_id)` is a read-only tool backed by the same local
SQLite repository. It returns related historical `tool_calls` and `approvals`
whose arguments contain that order ID. The final mock-mode diagnosis formats
those records into the summary, so a request about an order also mentions its
known local operational history. It is an internal POC audit helper, not a
replacement for an operational order, payment, fulfillment, or ticket system.

The audit trail stores structured events and tool metadata, not hidden
chain-of-thought.

## Deterministic mock data

Mock mode is fully local and deterministic. It does not call an external order,
payment, warehouse, or ticketing system. The following records are available
by order ID:

| Order | Order data | Payment | Fulfillment | Latest sync evidence |
|---|---|---|---|---|
| `100245` | paid, 2 items, warehouse `CZ-01` | captured | blocked, reason `sync_error` | `Warehouse API timeout` |
| `100246` | paid, 1 item, warehouse `CZ-02` | captured | shipped, tracking `CZ123456789` | `Shipment synced` |
| `100247` | paid, 4 items, warehouse `CZ-03` | captured | shipped, tracking `CZ987654321` | `Shipment synced` |
| `100248` | paid, 1 item, warehouse `CZ-01` | captured | pending, reason `awaiting_pick` | `Picking not started` |

Any other numeric order ID, for example `123456789`, produces a tool failure
because the order is not present in the mock data. A request without a numeric
order ID stops before tool calling and asks for one.

Useful demo requests:

```text
Investigate order 100245.
Investigate order 100245 and retry sync if it is blocked.
Investigate order 100245 and create an incident ticket.
Investigate order 100246.
Investigate order 100247.
Investigate order 100248.
Investigate order 999999.
Show me the current warehouse status.
```

Expected behavior:

- `100245` is the blocked synchronization case; diagnosis is automatic, while
  `retry_sync` and `create_ticket` pause for approval. Its final summary also
  includes related local SQLite audit records.
- `100246` and `100247` are healthy shipped-order cases and should complete
  without approval.
- `100248` is pending in picking and should produce evidence without requesting
  a synchronization retry.
- `999999` demonstrates an explicit not-found tool failure.
- The final request demonstrates input handling when no order ID is supplied.

## Inspect the audit database

After at least one run, the default `fabini.db` contains four related tables:
`runs`, `tool_calls`, `approvals`, and `events`. The database path can be
changed with `DATABASE_PATH` in `.env`.

The presentation helper lists recent runs and prints the latest run's summary,
findings, tool calls, approvals, and audit timeline:

```powershell
.\.venv\Scripts\python.exe scripts\browse_db.py
```

Inspect a specific run or use another database:

```powershell
.\.venv\Scripts\python.exe scripts\browse_db.py --run-id <run-id>
.\.venv\Scripts\python.exe scripts\browse_db.py --db path\to\other.db --limit 20
```

For a graphical alternative, open `fabini.db` in any SQLite browser. Do not
commit the file: local databases are covered by `.gitignore`.

## Safety boundaries

- Allow-list and Pydantic validation reject unknown tools and malformed input.
- The host rejects side-effect proposals that the user did not explicitly
  request; negated requests do not create approvals.
- Read-only failures may retry once; side-effecting tools never retry
  automatically.
- `MAX_ITERATIONS` bounds the model/tool loop.
- Side-effect action keys prevent duplicate execution.
- Approvals expire after 15 minutes and resolved approvals are idempotent.
- Timeouts and failures are explicit records, not successful fallbacks.
- Mock actions are simulated: `retry_sync` returns `queued`, and
  `create_ticket` creates an in-process ticket.

## Scenarios

The required two successful and one failing scenarios, including observed
Gemini behavior, are in [case_study.md](case_study.md).

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check app tests
.\.venv\Scripts\mypy app
```

Current verified result: 21 tests pass; Ruff lint, formatting, and mypy pass.
