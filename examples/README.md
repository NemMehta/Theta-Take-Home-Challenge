# Sample evaluation reports

Real artifacts emitted by this tool. The Ansible (Python) ones come from `task run`; the Go one from `task validate`.

## Ansible (Python / pytest) — `task run`

- **`report_gold_resolved.json`** — gold-patch solver with the function-level masker. Verdict **RESOLVED**: all 9 scored tests pass (1 fail-to-pass flips to passing, 8 pass-to-pass stay passing).
- **`report_noop_not_resolved.json`** — no-op solver (empty patch). Verdict **NOT RESOLVED** (`reason: f2p_not_passed`): the 1 fail-to-pass test fails while the 8 pass-to-pass tests pass — the failed-vs-passed artifact.
- **`report_command_isolation.json`** — command solver under `--network none`, function-level masker. The solver's `meta.stdout_tail` shows **`NET_BLOCKED`** (the container could not reach the network), and `mask_verification.scored_visible_after_mask` is 0.
- **`solver_patch_command.diff`** — the patch captured from that command-solver run: only the one-line edit to `lib/ansible/release.py`, with no modification or deletion of the scored test file (masking never leaks into the captured patch).
- **`report_anthropic_singleshot.json`** + **`solver_patch_anthropic.diff`** — single-shot LLM solver (`anthropic`, via `claude -p`). **8/9** (`reason: f2p_not_passed`): a real model attempt that compiles and keeps all 8 pass-to-pass green but does not fix the fail-to-pass. The captured patch touches only source files.
- **`report_agentic.json`** + **`solver_patch_agentic.diff`** — multi-turn LLM solver (`agentic`, Sonnet, file-only tools on a host copy). **7/9** (`reason: f2p_not_passed+p2p_regressed`): explored to the right file (`cli/adhoc.py`) but a signature change regressed a pass-to-pass. Source-only captured patch, no test edits.

## flipt (Go / `go test`) — `task validate`

- **`go_validate_report.json`** — Go bundle validate via `go test -json`. Verdict **VALID**: `TestLoad` **fails** at baseline and **passes** after the gold patch — the baseline-vs-gold guardrail on a non-Python repo, through the same verdict logic.
