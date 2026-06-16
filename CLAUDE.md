# Project: `task` — SWE-bench-style Task Bundle CLI
A Python CLI that packages a coding task (a repo at a commit + a problem statement + hidden tests) into a Docker container and runs/scores LLM solutions against it.

## Core concepts
- Bundle: a self-contained task directory (format below).
- Gold patch: the reference solution (touches source, never tests).
- Test patch: the new/modified tests the fix introduced.
- fail_to_pass (F2P): tests that fail on baseline, pass after the fix.
- pass_to_pass (P2P): pre-existing tests that pass before and after.
- Resolved = ALL F2P pass AND ALL P2P pass after applying a solution.

## Locked tech (do not change without asking)
- Python >= 3.10; Typer CLI; rich output.
- Shell out to the `docker` CLI via subprocess (NO docker SDK).
- Stdlib sqlite3 only (NO ORM). `git apply` for patches.

## Bundle format
task.json, description.md, gold_patch.diff, test_patch.diff, tests/fail_to_pass.json, tests/pass_to_pass.json

## Commands
init, validate, run, log, runs (plus `init --from-dataset <instance_id>`).

## Isolation (non-negotiable)
All repo/test/solver-output execution happens INSIDE the container; the host only orchestrates and never executes untrusted output. Test/solve phases run with `--network none` and resource limits (--memory, --cpus, --pids-limit). Bundles are copied in, not bind-mounted from sensitive host paths.

## Test-hiding (pluggable "masker")
Model must not see F2P/P2P during a run, but must see all other tests.
- Baseline (ship first): file-level — skip test_patch (hides F2P) and remove the scored test files (hides P2P).
- Upgrade (later): function-level — a Python `ast` masker removing only the named test functions, leaving other tests in the same file visible. File-level stays the fallback for non-Python.

## Solver (pluggable, tiered)
noop / gold / command stubs (default noop) → single-shot Anthropic (returns full changed-file contents; we git diff) → bounded agentic (stretch). Real model deferred; will use an Anthropic API key or `claude -p` (Agent SDK credit).

## Database
SQLite at ./.taskbundle/taskbundle.db. Tables: commands, runs (per-test detail in runs.results_json). The run's JSON report is also the evaluation-artifact deliverable.

## Reproducibility
Pin base commit + image (prefer digest). Log exact docker/git commands. One structured JSON report per run.

## Working style
Build in phases; implement ONLY the current phase; stubs until told otherwise; clear rich output and meaningful errors; ask before large or unrequested changes.

## Roadmap
P1 scaffold -> task init | P2 validate | P3 run + stubs + file-level masker + report + DB | P4 function-level masker | P5 real-model solver | P6 agentic
