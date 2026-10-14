"""Read-only presentation view for the local FABINI SQLite audit database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def configure_output() -> None:
    if (sys.stdout.encoding or "").lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_db = script_dir.parent / "fabini.db"

    parser = argparse.ArgumentParser(description="Browse FABINI agent runs and audit records.")
    parser.add_argument(
        "--db",
        default=str(default_db),
        help=f"SQLite database path (default: {default_db})",
    )
    parser.add_argument(
        "--run-id",
        help="Run ID to inspect (default: most recent run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of recent runs to list (default: 10)",
    )
    return parser.parse_args()


def print_run_list(db: sqlite3.Connection, limit: int) -> None:
    rows = db.execute(
        """
        SELECT run_id, mode, status, request, created_at
        FROM runs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    print("RECENT RUNS")
    print("-" * 100)
    for row in rows:
        print(
            f"{row['run_id'][:8]}  {row['status']:<17} {row['mode']:<6} "
            f"{row['created_at']}  {row['request']}"
        )


def choose_run_id(db: sqlite3.Connection, requested_run_id: str | None) -> str | None:
    if requested_run_id:
        row = db.execute(
            "SELECT run_id FROM runs WHERE run_id = ?",
            (requested_run_id,),
        ).fetchone()
        return row["run_id"] if row else None
    row = db.execute("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return row["run_id"] if row else None


def print_run_details(db: sqlite3.Connection, run_id: str) -> None:
    run = db.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        print(f"\nRun not found: {run_id}")
        return

    result: dict[str, Any] = json.loads(run["result_json"])
    print(f"\nRUN {run['run_id']}")
    print("-" * 100)
    print(f"Status:  {run['status']}    Mode: {run['mode']}")
    print(f"Request: {run['request']}")
    print(f"Summary: {result.get('summary') or '-'}")

    print("\nFINDINGS")
    for finding in result.get("findings", []) or ["-"]:
        print(f"- {finding}")

    print("\nTOOL CALLS")
    tool_calls = db.execute(
        """
        SELECT sequence, name, status, arguments_json, result_json, error
        FROM tool_calls
        WHERE run_id = ?
        ORDER BY sequence
        """,
        (run_id,),
    )
    for call in tool_calls:
        print(f"[{call['sequence']}] {call['name']} — {call['status']}")
        print(f"  arguments: {call['arguments_json']}")
        if call["error"]:
            print(f"  error: {call['error']}")
        elif call["result_json"]:
            print(f"  result: {call['result_json']}")

    print("\nAPPROVALS")
    approvals = db.execute(
        """
        SELECT tool_name, status, arguments_json, created_at, resolved_at
        FROM approvals
        WHERE run_id = ?
        ORDER BY created_at
        """,
        (run_id,),
    )
    approval_rows = list(approvals)
    if not approval_rows:
        print("-")
    for approval in approval_rows:
        print(
            f"{approval['tool_name']} — {approval['status']} — "
            f"{approval['created_at']} -> {approval['resolved_at'] or '-'}"
        )
        print(f"  arguments: {approval['arguments_json']}")

    print("\nAUDIT TIMELINE")
    events = db.execute(
        """
        SELECT sequence, event_type, payload_json, created_at
        FROM events
        WHERE run_id = ?
        ORDER BY sequence
        """,
        (run_id,),
    )
    for event in events:
        payload = json.loads(event["payload_json"])
        message = payload.get("message", event["event_type"])
        print(f"[{event['sequence']}] {event['created_at']}  {message}")


def main() -> None:
    configure_output()
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    database_path = Path(args.db)
    if not database_path.exists():
        raise SystemExit(f"Database not found: {database_path}")

    with sqlite3.connect(database_path) as db:
        db.row_factory = sqlite3.Row
        print_run_list(db, args.limit)
        run_id = choose_run_id(db, args.run_id)
        if run_id is None:
            print("\nNo runs recorded yet.")
            return
        print_run_details(db, run_id)


if __name__ == "__main__":
    main()
