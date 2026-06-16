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
IMPORTANT: in SWE-bench Pro the scored tests usually ALREADY EXIST at the base commit (confirmed on our instance: all 9 scored tests are present in test_adhoc.py at base). Hiding is therefore NOT achieved by "skipping the test patch" — it is done by the masker editing the BASELINE working tree:
- Baseline masker (ship first): file-level — delete each file in selected_test_files (hides every scored test in those files; also hides any unrelated test in the same file).
- Upgrade (Phase 4): function-level — parse each scored file with `ast` and remove ONLY the scored test functions/methods (matched to node IDs), leaving unrelated tests intact. File-level stays the fallback for non-Python.
The model edits source on the masked tree; scored tests are restored only for scoring (see staging).

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

## Environment notes
- Host venv: Python 3.14, with venv-local pip bootstrapped via get-pip.py (system python3-venv/pip aren't installed; sudo is non-interactive here). To recreate the venv:
  python3 -m venv .venv --without-pip && source .venv/bin/activate && curl -sS https://bootstrap.pypa.io/get-pip.py | python
- The host Python only runs the `task` CLI. Task repositories and their tests run INSIDE their Docker images with their own Python — host Python version does not affect them.
- Bleeding-edge Python 3.14 may lack wheels for some libraries (e.g. pyarrow/datasets). Prefer dependency-light, stdlib-based approaches on the host. For dataset access, use the Hugging Face datasets-server REST API via stdlib urllib, NOT the `datasets` package.

## Staging tests for scoring (validate/run)
Scoring runs the scored node IDs against the INSTANCE-commit version of the test files.
- Prefer: `git checkout <instance_commit> -- <selected test files>` (robust; overwrites any solver tampering; the prebuilt image has the instance commit in history).
- Fallback (for a self-contained bundle without the commit): `git apply test_patch.diff`.
- validate: fresh baseline -> stage instance test files -> run (expect F2P fail, P2P pass); then ALSO apply gold patch -> run (expect F2P pass, P2P pass).
- run scoring: fresh baseline -> apply SOLVER patch (source) -> stage instance test files -> run scored node IDs.

## Solver patch capture
The model works on the MASKED tree. Capture ONLY its source changes — exclude or restore the masked test files before diffing, so the masker's deletions don't leak into the solver patch. Scoring re-stages tests regardless, overwriting any model edits to test files.

## init is stateless
init verifies + discovers + records, then tears its container down. validate/run each start a fresh container. Discover (do NOT hardcode): the repo path inside the image, and a keep-alive invocation (the image's default entrypoint may be bash, which exits without a TTY). Record repo_path_in_container and image_digest into task.json.

## Test execution (pytest)
- The image's pytest is OLD (6.1.2). Do NOT rely on plugins (no pytest-json-report). For per-test pass/fail, run with built-in JUnit XML and parse it:
  python -m pytest <node_ids...> --junitxml=/tmp/pytest_report.xml -p no:cacheprovider -o cache_dir=/tmp/pytest_cache -rN
  run from repo_path_in_container; capture rc/stdout/stderr; then read & parse /tmp/pytest_report.xml.
- Outcome per <testcase>: child <failure> -> failed, <error> -> error, <skipped> -> skipped, none -> passed. "passed" means outcome==passed; an F2P "fails" = any non-passed outcome.
- Map <testcase> -> node ID by the leaf test name (substring after the last "::"); disambiguate Class::method IDs by checking the class appears in <testcase classname>. Any expected node ID with NO testcase -> "missing".
- Instance commit derived from instance_id via regex -([0-9a-f]{40})-v (confirmed in image history); shared helper, no hardcoding.
- Test staging for scoring: git checkout <instance_commit> -- <each selected_test_file> (preferred; overwrites tampering). git apply test_patch.diff is the fallback.
- This runner module is shared by validate and run.
