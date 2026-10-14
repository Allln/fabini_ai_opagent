# FABINI AI Operations Agent

## Short architecture description

FABINI AI Operations Agent is a local POC for investigating
e-commerce operational issues over mocked dataset (optionally test sandbox api could be used with proper dummy data).
A user submits a natural-language request about
an order. The agent gathers evidence from typed tools, records the steps and
tool calls, and returns a structured result. If the agent wants to perform a
side-effecting action, the application pauses and asks a human for approval.

The current tools are **in-process mock simulations**, not connections to live
warehouse, payment, order-management, or ticketing systems. Mock mode is an
intentional scripted expert walkthrough for the documented cases, while Gemini
mode demonstrates model-selected native function calls.

`get_order_audit` is an internal POC audit helper backed by local SQLite. It is
kept separate conceptually from the six operational tools in the case study.

## Product talking points

### 1. A focused operations agent, not a general chatbot

The implemented scope is intentionally narrow: investigate why an order was
not shipped, inspect payment and fulfillment state, inspect synchronization
logs, and optionally retry synchronization or create an incident ticket.

Product story ~ it matches the notes: a small agent
with a clearly defined job is easier to validate, control, and improve than a
general agent that attempts to operate across every company system.

### 2. Evidence-first workflow with human approval for actions

The agent first uses the four read-only tools:

- `get_order`
- `get_payment`
- `get_fulfillment`
- `get_sync_logs`

The two action tools require an explicit user request and are approval-gated:

- `retry_sync`
- `create_ticket`

The browser shows the requested action and its arguments before execution. A
user can approve or reject it. The deterministic mock does not decide whether
retrying is worthwhile; it recognizes explicit action wording and rejects
negated requests. The host applies the same explicit-intent check to Gemini
proposals before creating an approval. This demonstrates a practical
human-in-the-loop boundary without claiming that the system can safely change a
real external system yet.

### 3. Bounded and auditable tool calling

The host application, rather than the model alone, enforces the important
boundaries:

- only registered tools can run;
- Pydantic schemas validate tool arguments;
- side-effect proposals must match explicit user intent before approval;
- read-only failures may be retried once;
- side-effecting tools are not automatically retried;
- each tool call has a timeout;
- the model/tool loop has a configurable iteration limit;
- action keys prevent duplicate side-effect execution;
- approvals expire and repeated approval resolution is idempotent;
- runs, events, tool calls, approvals, errors, and results are stored in SQLite.

“agentic” behavior
is controlled by application code and produces an inspectable audit trail.

## What is implemented

1. The user opens a browser UI at `http://127.0.0.1:8000`.
2. The UI sends a request to `POST /api/runs`.
3. The selected adapter (mock or Gemini) returns text or a tool call.
4. The orchestrator validates the call and executes an allowed tool.
5. Tool results are sent back to the adapter for the next step.
6. A side-effecting call pauses at the approval gate.
7. The user approves or rejects the action through the UI.
8. The API returns a structured `AgentRunResult`; a tool failure produces a
   failed run status even when the model returns a final explanatory message.
9. SQLite stores the run snapshot and audit records.

The API also exposes:

- `GET /health`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/approvals/{approval_id}`

The UI displays the summary, findings, tool-call statuses, approval prompt, and
decision timeline.

## Technologies

- **Python 3.12** — application language.
- **FastAPI** — HTTP API and static-file serving.
- **Uvicorn** — local ASGI server.
- **Pydantic / pydantic-settings** — typed request, tool, result, approval,
  and configuration models.
- **Google Gemini API** — optional model adapter using native function calling.
- **SQLite** — local persistence for runs and audit data.
- **Vanilla HTML, CSS, and JavaScript** — small browser interface without a
  frontend framework.
- **pytest, Ruff, and mypy configuration** — testing, linting, formatting, and
  type-checking support.

The model proposes a tool call, but the host owns validation, execution,
approval, limits, and persistence. The model does not directly access SQLite or
an external system. The browser also renders a lightweight order-state graph
from the returned tool results; it does not change orchestration or add a
separate knowledge store.

## Directory layout

```text
.
├── app/
│   ├── agent/
│   │   ├── adapters.py
│   │   ├── orchestrator.py
│   │   └── policy.py
│   ├── db/
│   │   └── repository.py
│   ├── tools/
│   │   └── registry.py
│   ├── config.py
│   ├── errors.py
│   ├── main.py
│   └── models.py
├── scripts/
│   └── browse_db.py
├── static/
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── tests/
│   ├── test_agent.py
│   ├── test_adapters.py
│   └── test_api.py
├── .env.example
├── case_study.md
├── fabini.db                 # local runtime database, not a deployment DB
├── pyproject.toml
└── README.md
```

## Purpose of the core Python files

### `app/main.py`

Creates the FastAPI application, wires configuration, repository,
orchestrator, and tool registry, exposes the health/run/approval endpoints, and
serves the static browser UI.

### `app/config.py`

Loads and validates environment-backed settings such as mock/Gemini mode,
Gemini model, database path, iteration limit, tool timeout, host, and port.
It also keeps the shared Gemini system instruction and small policy defaults
such as approval timeout, read-only retry attempts, and model temperature.

### `app/models.py`

Defines the typed data contract for requests, model turns, tool calls, tool
results, approvals, timeline steps, run statuses, and final run results.

### `app/agent/adapters.py`

Provides the common model interface. `MockAgentModel` gives deterministic
multi-step behavior for local demos and tests. `GeminiAgentModel` uses the
Google Gemini API and native function calling when a key is configured.

### `app/agent/orchestrator.py`

Runs the bounded agent loop. It coordinates model turns and tools, records
steps, applies the approval gate, enforces iteration limits, handles failures,
and resumes an approved action.

### `app/agent/policy.py`

Centralizes the explicit action-intent check used before a side-effecting tool
can create an approval. It handles supported action phrases and direct
negation.

### `app/tools/registry.py`

Defines and allow-lists the seven tools, their Pydantic input schemas, mock
data, side-effect classification, timeout execution, and limited retry
behavior. `get_order_audit` uses the SQLite repository to read related
historical tool-call and approval records for the current order.

### `app/db/repository.py`

Persists run snapshots and audit data in SQLite. It stores events, tool calls,
approvals, statuses, timestamps, results, and errors.

### `app/errors.py`

Contains the application exceptions used for tool validation, tool execution,
and approval control flow.

### `scripts/browse_db.py`

Provides a command-line view of recent runs and the details of a selected run,
including findings, tool calls, approvals, and the audit timeline.

### Browser state graph

The result view presents a compact evidence graph for the detected order state.
It shows the order, payment, fulfillment, synchronization evidence, and any
requested action using only data already present in `AgentRunResult`. This is a
readability feature for the POC, not a general-purpose knowledge graph.

The complete workflows are shown in:

- [Deterministic mock workflow](poc-flow-deterministic.svg)
- [Gemini native tool-calling workflow](poc-flow-gemini.svg)

## Local host usage

The default local configuration is:

- host: `127.0.0.1`
- port: `8000`
- URL: `http://127.0.0.1:8000`
- mode: `mock`
- database: `fabini.db`

Start it from the `src` directory:

```powershell
..\.venv\Scripts\python.exe -m app.main
```

Mock mode needs no API key or network access. To use the optional Gemini
adapter, set `AGENT_MODE=gemini`, `GEMINI_API_KEY`, and optionally
`GEMINI_MODEL` in `.env`. The host remains responsible for validation and
approval in both modes.

To inspect persisted audit data:

```powershell
.\.venv\Scripts\python.exe scripts\browse_db.py
```

## What could be better and what is missing

These are not current capabilities; they are the boundary between this POC and
a production product. They are intentionally not implemented as part of this
local case-study scope:

- The tool timeout is a best-effort local boundary; Python cannot forcibly
  cancel a running thread or an already-started external request.

- **Real integrations:** replace in-process mock handlers with authenticated
  clients test sandbox for order, payment, fulfillment, synchronization, and ticket systems.
- **Authentication and authorization:** identify users and restrict which
  people may investigate, approve, or execute each action.
- **Durable execution:** move long-running work to a worker and persist enough
  state to resume after a process restart.
- **Production idempotency:** coordinate action keys with the target systems,
  not only with the local SQLite approval record.
- **Security hardening:** add deployment secrets management, audit access
  controls, rate limits, request-level authorization, and systematic
  prompt-injection testing.
- **Operational observability:** add structured service logs, metrics, tracing,
  alerting, and retention policies.
- **Data isolation:** add tenant/store boundaries and a production database
  strategy if multiple e-shops use the service.
- **Evaluation:** add a representative test set for correct diagnosis,
  unsafe requests, missing data, tool failures, and model regressions.
- **User experience:** show richer evidence and approval context while keeping
  the current concise timeline.

## TODO

### Product and scope

- Choose the first real operational integration and define its read/write
  contract.
- Decide which action types always require approval and which, if any, may be
  automatically executed.
- Define where a completed result must go: UI only, ticketing system, or an
  event/API consumed by another system.
- Define measurable success criteria for diagnosis accuracy, safe blocking, and
  time saved for operations colleagues.

### Engineering

- Implement authenticated integration clients behind the existing tool
  registry.
- Add authentication, authorization, tenant isolation, and secure secret
  handling.
- Add durable background execution and recovery of pending approvals.
- Add integration-level idempotency and contract tests.
- Add model evaluation cases and prompt-injection/abuse tests.
- Decide whether SQLite remains suitable after the first real deployment.

### Demo and communication

- Keep mock mode as the deterministic demo path.
- Present Gemini results as model-dependent demonstrations, not fixed
  guarantees.
- Use the successful diagnosis, approved retry, and missing-order failure
  scenarios from `case_study.md`.

## POC description

FABINI AI Operations Agent is a focused, auditable local POC that investigates
why an e-commerce order is blocked, uses typed tools to gather evidence, and
requires human approval before simulated operational actions.
