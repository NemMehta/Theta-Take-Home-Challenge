"""Typer CLI for the `task` tool.

Phase 1: stubs only. Each command prints a "not implemented yet" message and
exits cleanly. Flags, help text, and structure are final; bodies are not.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

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


@app.command()
def init(
    bundle: Path = typer.Option(..., "--bundle", help="Path to a task bundle directory."),
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
    _not_implemented("init")


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
