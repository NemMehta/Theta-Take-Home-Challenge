"""Pluggable, tiered solvers.

A solver runs on the MASKED tree and returns a SOURCE patch. Capture excludes the
masker's changes by restoring masked files before diffing, so the masker's
deletions never leak into the solver patch. Scoring re-stages real tests anyway.

Tiers: noop / gold / command stubs, plus a single-shot Anthropic solver driven by
the `claude` CLI in headless, tool-free mode (no API key; uses the CLI's OAuth).
The agentic solver is Phase 6.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class SolverResult:
    name: str
    patch: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[-n:])


def _capture_source_patch(handle, repo_path, base_commit, masked_files):
    """Restore masked files to baseline, stage everything, return the diff.

    Shared by the command and anthropic solvers: yields a SOURCE-only patch (the
    masker's deletions/edits are reverted first, so they never leak in).
    """
    rp = shlex.quote(repo_path)
    for tf in masked_files:
        handle.exec(f"git -C {rp} checkout {shlex.quote(base_commit)} -- {shlex.quote(tf)}")
    handle.exec(f"git -C {rp} add -A")
    drc, diff, derr = handle.exec(f"git -C {rp} diff --cached")
    return drc, diff, derr


class NoopSolver:
    name = "noop"

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask,
              selected_test_files, artifacts_dir=None) -> SolverResult:
        return SolverResult(name=self.name, patch="", meta={"note": "no-op solver"})


class GoldSolver:
    name = "gold"

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask,
              selected_test_files, artifacts_dir=None) -> SolverResult:
        patch = (Path(bundle_dir) / "gold_patch.diff").read_text(encoding="utf-8")
        return SolverResult(name=self.name, patch=patch, meta={"source": "gold_patch.diff"})


class CommandSolver:
    name = "command"

    def __init__(self, command: str | None):
        self.command = command

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask,
              selected_test_files, artifacts_dir=None) -> SolverResult:
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
        drc, diff, derr = _capture_source_patch(handle, repo_path, base_commit, mask.masked_files)
        if drc != 0:
            return SolverResult(name=self.name, patch="", meta=meta,
                                error=f"failed to capture diff: {derr.strip()}")
        return SolverResult(name=self.name, patch=diff, meta=meta)


# ---------------------------------------------------------------------------
# Single-shot Anthropic solver (via the `claude` CLI, headless + tool-free).
# ---------------------------------------------------------------------------

_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"_test\.py$"),
    re.compile(r"(^|/)conftest\.py$"),
)
_CLAUDE_TIMEOUT = 240
_MAX_CANDIDATES = 200
_MAX_LOCATE = 6
_FILE_HEAD_LINES = 1500
_FILE_MAX_BYTES = 60_000
_TOTAL_BUDGET = 250_000


def _is_test_path(path: str) -> bool:
    return any(p.search(path) for p in _TEST_PATTERNS)


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_]{4,}", text.lower())}


class AnthropicSolver:
    name = "anthropic"

    def solve(self, handle, repo_path, base_commit, bundle_dir, mask,
              selected_test_files, artifacts_dir=None) -> SolverResult:
        if shutil.which("claude") is None:
            return SolverResult(
                name=self.name,
                error="claude CLI not found; install Claude Code and run `claude auth login`",
            )

        bundle_dir = Path(bundle_dir)
        description = (bundle_dir / "description.md").read_text(encoding="utf-8")
        meta: dict[str, Any] = {"mechanism": "claude -p", "model": None,
                                "cost_usd": 0.0, "located_files": [],
                                "returned_files": [], "written_files": [],
                                "rejected": []}

        # ---- STEP A: LOCATE ----
        candidates, ranked = self._candidate_files(handle, repo_path, description)
        locate_prompt = self._locate_prompt(description, candidates)
        loc = self._call_claude(locate_prompt)
        self._dump(artifacts_dir, "solver_locate_prompt.txt", locate_prompt)
        self._dump(artifacts_dir, "solver_locate_response.txt", loc.get("raw", ""))
        if loc.get("error"):
            return SolverResult(name=self.name, meta=meta, error=loc["error"])
        meta["model"] = loc.get("model")
        meta["cost_usd"] = round(meta["cost_usd"] + loc.get("cost", 0.0), 6)

        located = self._parse_located(loc.get("text", ""), candidates)
        if not located:
            located = ranked[:3]
        meta["located_files"] = located

        # ---- STEP B: EDIT ----
        edit_prompt = self._edit_prompt(handle, repo_path, description, located)
        edt = self._call_claude(edit_prompt)
        self._dump(artifacts_dir, "solver_edit_prompt.txt", edit_prompt)
        self._dump(artifacts_dir, "solver_edit_response.txt", edt.get("raw", ""))
        if edt.get("error"):
            return SolverResult(name=self.name, meta=meta, error=edt["error"])
        meta["model"] = meta["model"] or edt.get("model")
        meta["cost_usd"] = round(meta["cost_usd"] + edt.get("cost", 0.0), 6)

        files = self._parse_file_blocks(edt.get("text", ""))
        meta["returned_files"] = list(files.keys())

        # ---- APPLY + GUARDRAILS ----
        for path, content in files.items():
            reason = self._reject_reason(path, repo_path, selected_test_files)
            if reason:
                meta["rejected"].append({"path": path, "reason": reason})
                continue
            self._write_file(handle, repo_path, path, content)
            meta["written_files"].append(path)

        if not meta["written_files"]:
            meta["note"] = "no usable source files returned by the model"
            return SolverResult(name=self.name, patch="", meta=meta)

        drc, diff, derr = _capture_source_patch(handle, repo_path, base_commit, mask.masked_files)
        if drc != 0:
            return SolverResult(name=self.name, patch="", meta=meta,
                                error=f"failed to capture diff: {derr.strip()}")
        return SolverResult(name=self.name, patch=diff, meta=meta)

    # -- candidate discovery --
    def _candidate_files(self, handle, repo_path, description):
        rc, out, _ = handle.exec(f"git -C {shlex.quote(repo_path)} ls-files '*.py'")
        paths = [p for p in out.splitlines() if p.strip()]
        paths = [p for p in paths if not _is_test_path(p)]
        words = _significant_words(description)
        scored = sorted(
            paths,
            key=lambda p: sum(1 for w in words if w in p.lower()),
            reverse=True,
        )
        candidates = scored[:_MAX_CANDIDATES] if len(scored) > _MAX_CANDIDATES else scored
        return candidates, scored

    # -- prompt builders --
    def _locate_prompt(self, description, candidates):
        listing = "\n".join(candidates)
        return (
            "You are fixing a bug in a software repository.\n\n"
            "## Problem statement\n" + description.strip() + "\n\n"
            "## Candidate source files (repo-relative)\n" + listing + "\n\n"
            "Which files must you READ to implement the fix? Respond with ONLY a "
            f"JSON array of at most {_MAX_LOCATE} repo-relative file paths chosen "
            "from the list above. No prose, no code fences — just the JSON array."
        )

    def _edit_prompt(self, handle, repo_path, description, located):
        parts = [
            "You are fixing a bug in a software repository.\n",
            "## Problem statement\n" + description.strip() + "\n",
            "## Current source files\n",
        ]
        budget = _TOTAL_BUDGET
        for path in located:
            rc, content, _ = handle.exec(
                f"cat {shlex.quote(repo_path)}/{shlex.quote(path)}")
            if rc != 0:
                continue
            note = ""
            if len(content.encode("utf-8", "ignore")) > _FILE_MAX_BYTES:
                lines = content.splitlines()[:_FILE_HEAD_LINES]
                content = "\n".join(lines)
                note = "  (TRUNCATED to head)"
            block = (f"=== BEGIN FILE: {path} ==={note}\n{content}\n"
                     f"=== END FILE: {path} ===\n")
            if len(block) > budget:
                break
            budget -= len(block)
            parts.append(block)
        parts.append(
            "\n## Instructions\n"
            "Implement the change. Return ONLY the complete, full revised contents "
            "of each file you modify, each wrapped exactly as:\n"
            "=== BEGIN FILE: <path> ===\n<entire new file content>\n"
            "=== END FILE: <path> ===\n"
            "Return nothing else. Do NOT modify test files. Do NOT include files "
            "you didn't change."
        )
        return "".join(parts)

    # -- claude invocation --
    def _call_claude(self, prompt: str) -> dict:
        """Run `claude -p` tool-free from a neutral cwd; parse JSON output."""
        tmpdir = tempfile.mkdtemp(prefix="taskbundle-claude-")
        ptmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            ptmp.write(prompt)
            ptmp.close()
            args = ["timeout", str(_CLAUDE_TIMEOUT), "claude", "-p",
                    "--output-format", "json", "--allowedTools", "", "--max-turns", "1"]
            with open(ptmp.name, "r", encoding="utf-8") as stdin_f:
                proc = subprocess.run(
                    args, cwd=tmpdir, stdin=stdin_f, capture_output=True, text=True,
                    timeout=_CLAUDE_TIMEOUT + 30,
                )
            raw = proc.stdout
            if proc.returncode == 124:
                return {"error": f"claude timed out after {_CLAUDE_TIMEOUT}s", "raw": raw}
            if proc.returncode != 0:
                combined = (proc.stdout + "\n" + proc.stderr).lower()
                if any(k in combined for k in ("auth", "login", "unauthorized", "credit", "oauth")):
                    return {"error": "claude auth/credit failure — run `claude auth login`",
                            "raw": proc.stdout + proc.stderr}
                return {"error": f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}",
                        "raw": proc.stdout + proc.stderr}
            return self._parse_claude_json(raw)
        except subprocess.TimeoutExpired:
            return {"error": f"claude timed out after {_CLAUDE_TIMEOUT}s", "raw": ""}
        finally:
            os.unlink(ptmp.name)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _parse_claude_json(self, raw: str) -> dict:
        try:
            data = json.loads(raw)
        except ValueError:
            return {"error": "could not parse claude JSON output", "raw": raw}
        if isinstance(data, dict) and data.get("is_error"):
            return {"error": f"claude reported error: {str(data.get('result'))[:300]}", "raw": raw}
        text = ""
        if isinstance(data, dict):
            text = data.get("result") or data.get("text") or ""
        cost = (data.get("total_cost_usd") or data.get("cost_usd") or 0.0) if isinstance(data, dict) else 0.0
        model = None
        if isinstance(data, dict):
            model = data.get("model") or (data.get("usage") or {}).get("model") \
                or (data.get("modelUsage") and next(iter(data["modelUsage"]), None))
        return {"text": text, "cost": cost or 0.0, "model": model, "raw": raw}

    # -- response parsers --
    def _parse_located(self, text, candidates):
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except ValueError:
            return []
        cand_set = set(candidates)
        return [p for p in arr if isinstance(p, str) and p in cand_set][:_MAX_LOCATE]

    def _parse_file_blocks(self, text):
        files = {}
        pattern = re.compile(
            r"=== BEGIN FILE: (.+?) ===[^\n]*\n(.*?)\n=== END FILE: \1 ===",
            re.DOTALL,
        )
        for m in pattern.finditer(text):
            files[m.group(1).strip()] = self._strip_code_fence(m.group(2))
        return files

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        """Drop a leading ```lang and trailing ``` fence the model may add."""
        lines = content.splitlines()
        if lines and re.match(r"^\s*```[\w.+-]*\s*$", lines[0]):
            lines = lines[1:]
            if lines and re.match(r"^\s*```\s*$", lines[-1]):
                lines = lines[:-1]
        return "\n".join(lines)

    # -- apply helpers --
    def _reject_reason(self, path, repo_path, selected_test_files):
        if path.startswith("/") or ".." in path.split("/"):
            return "not a safe repo-relative path"
        if path in selected_test_files:
            return "selected test file"
        if _is_test_path(path):
            return "test file"
        return None

    def _write_file(self, handle, repo_path, path, content):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        try:
            tmp.write(content)
            tmp.close()
            handle.cp_to(tmp.name, f"{repo_path}/{path}")
        finally:
            os.unlink(tmp.name)

    def _dump(self, artifacts_dir, name, content):
        if artifacts_dir is None:
            return
        try:
            (Path(artifacts_dir) / name).write_text(content or "", encoding="utf-8")
        except OSError:
            pass


def get_solver(name: str, command: str | None = None):
    if name == "noop":
        return NoopSolver()
    if name == "gold":
        return GoldSolver()
    if name == "command":
        return CommandSolver(command)
    if name == "anthropic":
        return AnthropicSolver()
    raise ValueError(f"unknown solver: {name}")
