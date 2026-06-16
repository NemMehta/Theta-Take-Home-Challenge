# Sample evaluation reports

Real reports emitted by `task run` on the included Ansible instance.

- **`report_gold_resolved.json`** — gold-patch solver with the function-level masker. Verdict **RESOLVED**: all 9 scored tests pass (1 fail-to-pass flips to passing, 8 pass-to-pass stay passing).
- **`report_noop_not_resolved.json`** — no-op solver (empty patch). Verdict **NOT RESOLVED** (`reason: f2p_not_passed`): the 1 fail-to-pass test fails while the 8 pass-to-pass tests pass — the failed-vs-passed artifact.
- **`report_command_isolation.json`** — command solver under `--network none`, with the function-level masker. The solver's `meta.stdout_tail` shows **`NET_BLOCKED`** (the container could not reach the network), and `mask_verification.scored_visible_after_mask` is 0.
- **`solver_patch_command.diff`** — the patch captured from that command-solver run: only the one-line edit to `lib/ansible/release.py`, with no modification or deletion of the scored test file (masking never leaks into the captured patch).
