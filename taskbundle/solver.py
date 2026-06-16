"""Pluggable, tiered solvers (Phase 3: stub tier).

A solver runs on the MASKED tree and returns a SOURCE patch. Capture excludes the
masker's changes by restoring masked files before diffing, so the masker's
deletions never leak into the solver patch. Scoring re-stages real tests anyway.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SolverResult:
    name: str
    patch: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[-n:])


class NoopSolver:
    name = "noop"

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask, selected_test_files) -> SolverResult:
        return SolverResult(name=self.name, patch="", meta={"note": "no-op solver"})


class GoldSolver:
    name = "gold"

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask, selected_test_files) -> SolverResult:
        patch = (Path(bundle_dir) / "gold_patch.diff").read_text(encoding="utf-8")
        return SolverResult(name=self.name, patch=patch, meta={"source": "gold_patch.diff"})


class CommandSolver:
    name = "command"

    def __init__(self, command: str | None):
        self.command = command

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask, selected_test_files) -> SolverResult:
        if not self.command:
            return SolverResult(
                name=self.name, error="--solver-cmd required for the command solver"
            )
        rc, out, err = handle.exec(self.command, workdir=repo_path, timeout=900)
        meta = {
            "command": self.command,
            "rc": rc,
            "stdout_tail": _tail(out),
            "stderr_tail": _tail(err),
        }
        # Capture the SOURCE patch, excluding the masker's deletions: restore each
        # masked file to baseline, then diff the staged working tree.
        rp = shlex.quote(repo_path)
        for tf in mask.masked_files:
            handle.exec(f"git -C {rp} checkout {shlex.quote(base_commit)} -- {shlex.quote(tf)}")
        handle.exec(f"git -C {rp} add -A")
        drc, diff, derr = handle.exec(f"git -C {rp} diff --cached")
        if drc != 0:
            return SolverResult(name=self.name, patch="", meta=meta,
                                error=f"failed to capture diff: {derr.strip()}")
        return SolverResult(name=self.name, patch=diff, meta=meta)


def get_solver(name: str, command: str | None = None):
    if name == "noop":
        return NoopSolver()
    if name == "gold":
        return GoldSolver()
    if name == "command":
        return CommandSolver(command)
    if name == "anthropic":
        raise NotImplementedError("anthropic solver arrives in Phase 5")
    raise ValueError(f"unknown solver: {name}")
