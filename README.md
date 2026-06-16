# task (taskbundle)

A CLI that packages a SWE-bench-style coding task into a Docker container, hides the scored tests from a solver, runs a solver (LLM or stub), and scores the result — recording every run in a queryable database.

## What it does

- **`task init`** — builds a self-contained task bundle from a SWE-Bench Pro instance (`--from-dataset`), then (given an existing bundle) starts the instance's container, discovers where the repo lives inside it, normalizes a clean baseline, confirms the scored tests collect, checks the gold/test patches apply, and records the repo path + image digest into `task.json`.
- **`task validate`** — proves a bundle is well-formed by running its scored tests twice in the container: at baseline (expect the fail-to-pass test to fail and every pass-to-pass test to pass) and after applying the gold patch (expect all to pass). Prints a per-test table and a `VALID`/`INVALID` verdict.
- **`task run`** — masks the scored tests from the solver, runs the chosen solver, applies the solver's patch to a **fresh** baseline, re-stages the real test files, runs the scored node IDs, and reports `resolved = all fail-to-pass pass AND all pass-to-pass pass`. Emits one JSON report per run.
- **`task runs` / `task log`** — query the database: list recent runs, or show the full record (and per-node results) for a run id or command id.

## Requirements

- **Docker.** On Windows, use Docker Desktop with WSL2 integration and run everything inside the WSL/Ubuntu shell.
- **Python 3.10+** on the host (the host only orchestrates; the repo's own tests run inside the image with the image's Python).
- **The instance's pre-built image** (pulled in the Quickstart below).

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

If `python3-venv` is unavailable (no interactive sudo), create the venv without pip and bootstrap it: `python3 -m venv .venv --without-pip && source .venv/bin/activate && curl -sS https://bootstrap.pypa.io/get-pip.py | python`, then `pip install -e .`.

## Quickstart

The example bundle for the demo instance is already included in this repo, so you can skip straight to pulling the image and running. (It can be rebuilt from scratch with `task init --from-dataset instance_ansible__ansible-cb94c0cc550df9e98f1247bc71d8c2b861c75049-v1055803c3a812189a1133297f7f5468579283f86`.)

Throughout, `BUNDLE` is the included bundle directory:

```bash
BUNDLE=bundles/instance_ansible__ansible-cb94c0cc550df9e98f1247bc71d8c2b861c75049-v1055803c3a812189a1133297f7f5468579283f86
```

1. The bundle is already present under `bundles/` — no build step needed.
2. Pull the pre-built image:
   ```bash
   docker pull jefzda/sweap-images:ansible.ansible-ansible__ansible-cb94c0cc550df9e98f1247bc71d8c2b861c75049-v1055803c3a812189a1133297f7f5468579283f86
   ```
3. Verify the container/environment and record metadata:
   ```bash
   task init --bundle "$BUNDLE"
   ```
4. Validate the bundle (baseline vs gold) — expect **VALID**:
   ```bash
   task validate --bundle "$BUNDLE"
   ```
5. Solve with the gold patch and the function-level masker — expect **RESOLVED**:
   ```bash
   task run --bundle "$BUNDLE" --solver gold --mask function
   ```
6. Solve with the no-op solver — expect **NOT RESOLVED** (the fail-to-pass test still fails):
   ```bash
   task run --bundle "$BUNDLE" --solver noop
   ```
7. Isolation demo — a command solver that tries the network and edits a source file. Expect `NET_BLOCKED` in the report's solver meta and a captured patch containing only the source edit:
   ```bash
   task run --bundle "$BUNDLE" --solver command --mask function --solver-cmd "python -c \"import socket; socket.setdefaulttimeout(5); socket.create_connection(('1.1.1.1',53))\" 2>/dev/null && echo NET_OK || echo NET_BLOCKED; echo '# touched by solver' >> lib/ansible/release.py"
   ```
8. Query the database:
   ```bash
   task runs
   task log --id <run_id>
   ```

## Commands and flags

```
task init      --bundle PATH        Output bundle dir (with --from-dataset) or task bundle dir
               --repo TEXT           Git repo URL
               --commit TEXT         Base commit SHA
               --image TEXT          Prebuilt docker image reference
               --from-dataset TEXT   SWE-Bench Pro instance_id to scaffold a bundle from

task validate  --bundle PATH                       (required) task bundle directory
               --json PATH                          Write a machine-readable result here
               --keep-container / --rm-container     Keep/remove the container (default: --rm-container)

task run       --bundle PATH                              (required) task bundle directory
               --solver [noop|gold|command|anthropic]      Solver backend (default: noop)
               --solver-cmd TEXT                           Command for the 'command' solver
               --mask [file|function]                      Test-hiding strategy (default: file)
               --out PATH                                  Write the JSON report here
               --no-network / --network                    Disable container networking (default: --no-network)
               --keep-container / --rm-container            Keep/remove the container (default: --rm-container)

task log       --id TEXT            (required) a command_id or run_id to show

task runs      --limit INTEGER      Maximum number of runs to list (default: 20)
```

Note: `init --from-dataset <id>` builds a bundle; `init --bundle <dir>` (without `--from-dataset`) runs the container-side verification. The demo uses `--mask function`; the default mask is `file`.

## Bundle format

```
<bundle>/
├── task.json                 # metadata (see below)
├── description.md            # problem statement (+ requirements/interface)
├── gold_patch.diff           # reference source fix (never touches tests)
├── test_patch.diff           # the instance's test changes
└── tests/
    ├── fail_to_pass.json      # node IDs that fail at baseline, pass after the fix
    └── pass_to_pass.json      # node IDs that pass before and after the fix
```

`task.json` fields:

| field | meaning |
|-------|---------|
| `schema_version` | bundle schema version |
| `instance_id` | SWE-Bench Pro instance id |
| `source` | dataset origin (`swe-bench-pro`) |
| `repo` / `repo_url` | source repository |
| `base_commit` | pinned commit the task is evaluated against |
| `language` | repo language (lowercased) |
| `image` | pre-built Docker image reference |
| `image_digest` | image sha256 digest (filled by `init`; pins reproducibility) |
| `repo_path_in_container` | where the repo lives in the image (discovered by `init`) |
| `before_repo_set_cmd` | the dataset's baseline-setup commands (preserved as-is) |
| `test.runner` / `test.selected_test_files` | test runner and scored test files |
| `counts.fail_to_pass` / `counts.pass_to_pass` | scored-test counts |
| `created_at` | bundle creation timestamp (ISO-8601 UTC) |

## How it works

The host only issues `docker`/`git` commands and reads results back; all untrusted work (applying a patch, running tests, running a command solver) happens inside the container. A run **solves** then **scores** against a fresh baseline: it masks the scored tests, lets the solver produce a source patch, then resets to a pristine tree, applies the patch, re-stages the real tests, and runs the scored node IDs — so masking can never affect the score. Masking is pluggable: file-level deletes whole scored test files (language-agnostic but over-hides), while function-level uses Python's `ast` to remove only the scored functions/methods (preserving unrelated tests, with a file-level fallback). Tests run via pytest with `--forked` (per-test process isolation) and JUnit XML output parsed into a per-node outcome map. The run container uses `--network none` plus memory/CPU/PID limits, and the bundle pins both the base commit and the image digest. See DESIGN_NOTES.md for the tradeoffs behind these choices.

## Sample reports

The `examples/` directory contains real reports from this tool:

- `report_gold_resolved.json` — a gold-patch run: **RESOLVED** (all 9 scored tests pass).
- `report_noop_not_resolved.json` — a no-op run: **NOT RESOLVED** (the 1 fail-to-pass test fails, 8 pass-to-pass pass) — the failed-vs-passed artifact.
- `report_command_isolation.json` + `solver_patch_command.diff` — the isolation demo: `NET_BLOCKED` in the solver meta and a captured patch with no test-file leakage.

See `examples/README.md` for details.

## Limitations / future work

- Function-level masking is Python-only; other languages fall back to file-level deletion.
- The noop/gold/command solvers are implemented; a single-shot Anthropic solver and a bounded agentic solver are planned (`--solver anthropic` is currently a stub).
- Validated end to end on one SWE-Bench Pro Python instance. The design is language/runner-agnostic (repo path discovered, runner declared in `task.json`), but only the pytest runner is implemented so far.
