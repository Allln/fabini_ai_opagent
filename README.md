# FABINI AI Operations Agent

Proof of concept for an operations agent that investigates e-commerce
requests, calls typed tools, requires approval for side effects, and returns a
structured audit trail.

The project uses Python 3.12, FastAPI, Pydantic, SQLite, vanilla HTML/CSS/JS,
and Google's Gemini API. The operational systems are simulated in-process:
this is not a production integration.

## Run locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m app.main
```

Open <http://127.0.0.1:8000>. Mock mode is the default and needs no key or
network. For a runtime-only install, use
`python -m pip install -r requirements.txt`.

## Gemini mode

Set these values in `.env`:

```dotenv
AGENT_MODE=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash
```

Gemini uses native function calling. The host application still validates tool
names and arguments, records every call, limits iterations, and gates
side-effecting calls. A missing key produces an explicit failure; it is never
returned to the browser. Gemini results are model-dependent; the observed
manual runs are recorded in [case_study.md](case_study.md).

## Architecture and decisions

- [app/main.py](app/main.py) exposes the FastAPI API and serves the browser UI.
- [app/agent/adapters.py](app/agent/adapters.py) provides Gemini and
  deterministic mock adapters behind one interface.
- [app/agent/orchestrator.py](app/agent/orchestrator.py) owns the bounded
  multi-step loop, approvals, idempotency, and structured outcomes.
- [app/tools/registry.py](app/tools/registry.py) validates and executes the
  six assignment tools:
  `get_order`, `get_payment`, `get_fulfillment`, `get_sync_logs`, `retry_sync`,
  and `create_ticket`.
- [app/db/repository.py](app/db/repository.py) persists runs, events, tool
  calls, and approvals in SQLite.

Read-only `get_*` tools run automatically. `retry_sync` and `create_ticket`
require approval because they have side effects. Mock mode was chosen because
it makes the same flow reproducible and testable without credentials; Gemini
was added to demonstrate real tool calling. SQLite was chosen to keep the POC
auditable and local; the UI is framework-free to keep the demo small.

The audit trail stores structured events and tool metadata, not hidden
chain-of-thought.

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

Current verified result: 10 tests pass; Ruff lint and formatting pass. Mypy is
included, but its executable was blocked by the local Windows Application
Control policy, so no successful mypy result is claimed.
