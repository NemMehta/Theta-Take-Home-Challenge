"""SQLite persistence layer (stdlib sqlite3 only, no ORM).

The database lives at ./.taskbundle/taskbundle.db. The directory and tables are
created on first use. Callers generate ids and ISO-8601 UTC timestamp strings.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DB_DIR = Path(".taskbundle")
DB_PATH = DB_DIR / "taskbundle.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    command_id  TEXT PRIMARY KEY,
    command     TEXT,
    args_json   TEXT,
    bundle      TEXT,
    status      TEXT,
    message     TEXT,
    started_at  TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    command_id    TEXT,
    instance_id   TEXT,
    solver        TEXT,
    image         TEXT,
    commit_sha    TEXT,
    resolved      INTEGER,
    f2p_total     INTEGER,
    f2p_passed    INTEGER,
    p2p_total     INTEGER,
    p2p_passed    INTEGER,
    results_json  TEXT,
    patch_applied INTEGER,
    report_path   TEXT,
    started_at    TEXT,
    finished_at   TEXT
);
"""


def _connect() -> sqlite3.Connection:
    """Open a connection with dict-like rows, ensuring the DB dir exists."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the .taskbundle dir and both tables if they do not exist."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def record_command(
    command_id: str,
    command: str,
    args_json: str,
    bundle: Optional[str] = None,
    status: Optional[str] = None,
    message: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    """Insert or replace a row in `commands`."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO commands (
                command_id, command, args_json, bundle,
                status, message, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                command,
                args_json,
                bundle,
                status,
                message,
                started_at,
                finished_at,
            ),
        )


def record_run(
    run_id: str,
    command_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    solver: Optional[str] = None,
    image: Optional[str] = None,
    commit_sha: Optional[str] = None,
    resolved: Optional[int] = None,
    f2p_total: Optional[int] = None,
    f2p_passed: Optional[int] = None,
    p2p_total: Optional[int] = None,
    p2p_passed: Optional[int] = None,
    results_json: Optional[str] = None,
    patch_applied: Optional[int] = None,
    report_path: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
) -> None:
    """Insert or replace a row in `runs`."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, command_id, instance_id, solver, image, commit_sha,
                resolved, f2p_total, f2p_passed, p2p_total, p2p_passed,
                results_json, patch_applied, report_path, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                command_id,
                instance_id,
                solver,
                image,
                commit_sha,
                resolved,
                f2p_total,
                f2p_passed,
                p2p_total,
                p2p_passed,
                results_json,
                patch_applied,
                report_path,
                started_at,
                finished_at,
            ),
        )


def get_command(command_id: str) -> Optional[sqlite3.Row]:
    """Return the `commands` row with this id, or None."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        )
        return cur.fetchone()


def get_run(run_id: str) -> Optional[sqlite3.Row]:
    """Return the `runs` row with this id, or None."""
    init_db()
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return cur.fetchone()


def list_runs(limit: int = 20) -> list[sqlite3.Row]:
    """Return up to `limit` most-recent runs (newest first)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()


def list_commands(limit: int = 20) -> list[sqlite3.Row]:
    """Return up to `limit` most-recent commands (newest first)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM commands ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()
