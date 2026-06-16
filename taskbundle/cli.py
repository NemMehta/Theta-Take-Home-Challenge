"""Typer CLI for the `task` tool.

Phase 1: stubs only. Each command prints a "not implemented yet" message and
exits cleanly. Flags, help text, and structure are final; bodies are not.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from taskbundle import bundle as bundle_mod
from taskbundle import db
from taskbundle.dataset import DatasetError, find_row

app = typer.Typer(
    name="task",
    help="Package SWE-bench-style coding tasks into Docker containers and run/score LLM solutions.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


class Solver(str, Enum):
    """Available solver backends."""

    noop = "noop"
    gold = "gold"
    command = "command"
    anthropic = "anthropic"


def _not_implemented(command: str) -> None:
    """Print the standard stub message and exit 0."""
    console.print(f"[yellow]{command} not implemented yet[/yellow]")
    raise typer.Exit(code=0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.command()
def init(
    bundle: Optional[Path] = typer.Option(
        None, "--bundle", help="Output bundle directory (with --from-dataset) or task bundle directory."
    ),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git repo URL."),
    commit: Optional[str] = typer.Option(None, "--commit", help="Base commit SHA."),
    image: Optional[str] = typer.Option(None, "--image", help="Prebuilt docker image reference."),
    from_dataset: Optional[str] = typer.Option(
        None,
        "--from-dataset",
        help="SWE-Bench Pro instance_id to scaffold a bundle from.",
    ),
) -> None:
    """Create or scaffold a task bundle."""
    if from_dataset is None:
        _not_implemented("init: container initialization")
    _init_from_dataset(from_dataset, bundle)


def _init_from_dataset(instance_id: str, bundle: Optional[Path]) -> None:
    """Build a task bundle from a SWE-Bench Pro dataset row."""
    out_dir = Path(bundle) if bundle else bundle_mod.default_bundle_dir(instance_id)
    command_id = uuid.uuid4().hex
    started_at = _now_iso()
    args_json = json.dumps(
        {"from_dataset": instance_id, "bundle": str(out_dir)}, sort_keys=True
    )

    db.init_db()
    try:
        console.print(f"Fetching dataset row for [cyan]{instance_id}[/cyan] …")
        item = find_row(instance_id)
        summary = bundle_mod.build_bundle(item, out_dir)
    except (DatasetError, ValueError, KeyError) as e:
        db.record_command(
            command_id=command_id,
            command="init",
            args_json=args_json,
            bundle=str(out_dir),
            status="error",
            message=str(e)[:500],
            started_at=started_at,
            finished_at=_now_iso(),
        )
        console.print(f"[red]init failed:[/red] {e}")
        raise typer.Exit(code=1)

    db.record_command(
        command_id=command_id,
        command="init",
        args_json=args_json,
        bundle=summary["bundle_dir"],
        status="success",
        message=f"bundle built: {summary['n_f2p']} F2P / {summary['n_p2p']} P2P",
        started_at=started_at,
        finished_at=_now_iso(),
    )
    console.print(f"[green]✓ bundle written to[/green] [bold]{summary['bundle_dir']}[/bold]")
    console.print(f"  image: {summary['image']}")
    console.print(
        f"  fail_to_pass: {summary['n_f2p']}  pass_to_pass: {summary['n_p2p']}"
        f"  test_files: {summary['n_test_files']}"
    )


@app.command()
def validate(
    bundle: Path = typer.Option(..., "--bundle", help="Path to a task bundle directory."),
    json: Optional[Path] = typer.Option(
        None, "--json", help="Write a machine-readable result to this path."
    ),
    keep_container: bool = typer.Option(
        False,
        "--keep-container/--rm-container",
        help="Keep the container after validation instead of removing it.",
    ),
) -> None:
    """Validate a task bundle (baseline + gold patch reproduce the expected results)."""
    _not_implemented("validate")


@app.command()
def run(
    bundle: Path = typer.Option(..., "--bundle", help="Path to a task bundle directory."),
    solver: Solver = typer.Option(
        Solver.noop, "--solver", help="Solver backend to produce a solution."
    ),
    solver_cmd: Optional[str] = typer.Option(
        None, "--solver-cmd", help="Command to run for the 'command' solver."
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Write the JSON report to this path."
    ),
    no_network: bool = typer.Option(
        True,
        "--no-network/--network",
        help="Disable container networking during the run.",
    ),
    keep_container: bool = typer.Option(
        False,
        "--keep-container/--rm-container",
        help="Keep the container after the run instead of removing it.",
    ),
) -> None:
    """Run a solver against a bundle and score the result."""
    _not_implemented("run")


@app.command()
def log(
    id: str = typer.Option(..., "--id", help="A command_id or run_id to show the log for."),
) -> None:
    """Show the recorded log for a command or run."""
    _not_implemented("log")


@app.command()
def runs(
    limit: int = typer.Option(20, "--limit", help="Maximum number of runs to list."),
) -> None:
    """List recent runs."""
    _not_implemented("runs")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
